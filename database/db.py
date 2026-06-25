import asyncpg
import json
from datetime import datetime
from config import Config


_pool = None


async def get_pool() -> asyncpg.Pool:
    """Retourne le pool de connexions PostgreSQL (singleton)."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=Config.DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
    return _pool


async def close_pool():
    """Ferme le pool de connexions proprement."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        print("[DB] Pool de connexions fermé.")


async def init_db() -> bool:
    """
    Initialise la base de données : crée les tables si elles n'existent pas.
    Appelé au démarrage du système.
    """
    print("[DB] Initialisation de la base de données...")

    create_signals_table = """
        CREATE TABLE IF NOT EXISTS signals (
            id SERIAL PRIMARY KEY,
            asset VARCHAR(20) NOT NULL,
            asset_type VARCHAR(10) NOT NULL,         -- 'crypto' ou 'stock'
            score INTEGER NOT NULL,
            signal_count INTEGER NOT NULL,
            signals_detected JSONB NOT NULL,
            price_at_signal NUMERIC(20, 8),
            sources JSONB,
            ai_analysis TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """

    create_performance_table = """
        CREATE TABLE IF NOT EXISTS performance (
            id SERIAL PRIMARY KEY,
            signal_id INTEGER REFERENCES signals(id),
            asset VARCHAR(20) NOT NULL,
            entry_price NUMERIC(20, 8),
            target_price NUMERIC(20, 8),
            stop_price NUMERIC(20, 8),
            price_24h NUMERIC(20, 8),
            price_7d NUMERIC(20, 8),
            price_30d NUMERIC(20, 8),
            outcome VARCHAR(20),                     -- 'hit_target', 'hit_stop', 'neutral'
            return_pct NUMERIC(8, 4),
            checked_at TIMESTAMPTZ DEFAULT NOW()
        );
    """

    create_system_log_table = """
        CREATE TABLE IF NOT EXISTS system_log (
            id SERIAL PRIMARY KEY,
            level VARCHAR(10) NOT NULL,              -- 'INFO', 'WARNING', 'ERROR'
            module VARCHAR(50),
            message TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(create_signals_table)
            await conn.execute(create_performance_table)
            await conn.execute(create_system_log_table)

        print("[DB] Tables créées ou déjà existantes.")
        return True

    except Exception as e:
        print(f"[DB] ERREUR lors de l'initialisation : {e}")
        return False


async def save_signal(
    asset: str,
    asset_type: str,
    score: int,
    signals_detected: list,
    price: float,
    sources: list,
    ai_analysis: str,
    entry_price: float = None,
    target_price: float = None,
    stop_price: float = None,
) -> int | None:
    """
    Enregistre un signal qualifié dans la base de données.
    Retourne l'ID du signal inséré, ou None en cas d'erreur.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO signals
                    (asset, asset_type, score, signal_count, signals_detected,
                     price_at_signal, sources, ai_analysis)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                asset,
                asset_type,
                score,
                len(signals_detected),
                json.dumps(signals_detected),
                price,
                json.dumps(sources),
                ai_analysis,
            )

            signal_id = row["id"]

            if entry_price or target_price or stop_price:
                await conn.execute(
                    """
                    INSERT INTO performance (signal_id, asset, entry_price, target_price, stop_price)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    signal_id,
                    asset,
                    entry_price,
                    target_price,
                    stop_price,
                )

            return signal_id

    except Exception as e:
        print(f"[DB] ERREUR save_signal ({asset}) : {e}")
        return None


async def log_system(level: str, module: str, message: str):
    """Enregistre un événement système dans la base de données."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO system_log (level, module, message) VALUES ($1, $2, $3)",
                level.upper(),
                module,
                message,
            )
    except Exception as e:
        print(f"[DB] ERREUR log_system : {e}")


async def test_connection() -> bool:
    """Teste la connexion à PostgreSQL. Retourne True si OK."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
            if result == 1:
                print("[DB] Connexion PostgreSQL : OK")
                return True
    except Exception as e:
        print(f"[DB] ERREUR connexion PostgreSQL : {e}")
    return False
