import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

from database.db import get_pool, log_system


# ─── Constantes ───────────────────────────────────────────────────────────────

BINANCE_REST = "https://api.binance.com"
YAHOO_BASE = "https://query1.finance.yahoo.com"

# Delais de verification apres le signal
CHECK_DELAYS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


# ─── Recuperation des prix ────────────────────────────────────────────────────

async def fetch_current_price_crypto(
    session: aiohttp.ClientSession,
    symbol: str,
) -> float | None:
    """Recupere le prix actuel d'une paire crypto sur Binance."""
    try:
        async with session.get(
            f"{BINANCE_REST}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return float(data["price"])
    except Exception as e:
        print(f"[PERF] ERREUR prix crypto {symbol} : {e}")
        return None


async def fetch_current_price_stock(
    session: aiohttp.ClientSession,
    symbol: str,
) -> float | None:
    """Recupere le prix actuel d'une action via Yahoo Finance."""
    try:
        async with session.get(
            f"{YAHOO_BASE}/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        return float(price) if price else None

    except Exception as e:
        print(f"[PERF] ERREUR prix stock {symbol} : {e}")
        return None


# ─── Logique de verification ──────────────────────────────────────────────────

def compute_outcome(
    entry_price: float,
    current_price: float,
    target_price: float | None,
    stop_price: float | None,
) -> tuple[str, float]:
    """
    Calcule le resultat d'un signal.
    Retourne (outcome, return_pct).
    outcome : 'hit_target' | 'hit_stop' | 'positive' | 'negative' | 'neutral'
    """
    if entry_price <= 0:
        return "unknown", 0.0

    return_pct = (current_price - entry_price) / entry_price * 100

    if target_price and current_price >= target_price:
        return "hit_target", round(return_pct, 2)
    if stop_price and current_price <= stop_price:
        return "hit_stop", round(return_pct, 2)
    if return_pct >= 2:
        return "positive", round(return_pct, 2)
    if return_pct <= -2:
        return "negative", round(return_pct, 2)

    return "neutral", round(return_pct, 2)


# ─── Verification des signaux en attente ──────────────────────────────────────

async def check_pending_signals():
    """
    Verifie les signaux dont les delais 24h / 7j / 30j sont echus.
    Met a jour la table performance avec les prix reels.
    """
    pool = await get_pool()

    async with aiohttp.ClientSession() as session:
        async with pool.acquire() as conn:

            # Signaux sans verification 24h
            rows_24h = await conn.fetch("""
                SELECT s.id, s.asset, s.asset_type, s.created_at,
                       p.id as perf_id, p.entry_price, p.target_price, p.stop_price,
                       p.price_24h, p.price_7d, p.price_30d
                FROM signals s
                JOIN performance p ON p.signal_id = s.id
                WHERE s.created_at <= NOW() - INTERVAL '24 hours'
                  AND p.price_24h IS NULL
                LIMIT 20
            """)

            # Signaux sans verification 7j
            rows_7d = await conn.fetch("""
                SELECT s.id, s.asset, s.asset_type, s.created_at,
                       p.id as perf_id, p.entry_price, p.target_price, p.stop_price,
                       p.price_24h, p.price_7d, p.price_30d
                FROM signals s
                JOIN performance p ON p.signal_id = s.id
                WHERE s.created_at <= NOW() - INTERVAL '7 days'
                  AND p.price_24h IS NOT NULL
                  AND p.price_7d IS NULL
                LIMIT 20
            """)

            # Signaux sans verification 30j
            rows_30d = await conn.fetch("""
                SELECT s.id, s.asset, s.asset_type, s.created_at,
                       p.id as perf_id, p.entry_price, p.target_price, p.stop_price,
                       p.price_24h, p.price_7d, p.price_30d
                FROM signals s
                JOIN performance p ON p.signal_id = s.id
                WHERE s.created_at <= NOW() - INTERVAL '30 days'
                  AND p.price_7d IS NOT NULL
                  AND p.price_30d IS NULL
                LIMIT 20
            """)

        updated_24h = await _process_checks(session, pool, rows_24h, "24h")
        updated_7d = await _process_checks(session, pool, rows_7d, "7d")
        updated_30d = await _process_checks(session, pool, rows_30d, "30d")

        total = updated_24h + updated_7d + updated_30d
        if total > 0:
            print(f"[PERF] {total} signaux mis a jour "
                  f"(24h:{updated_24h}, 7j:{updated_7d}, 30j:{updated_30d}).")
            await log_system("INFO", "performance",
                             f"{total} verifications effectuees.")


async def _process_checks(
    session: aiohttp.ClientSession,
    pool,
    rows: list,
    delay: str,
) -> int:
    """Traite une liste de signaux a verifier pour un delai donne."""
    if not rows:
        return 0

    updated = 0
    price_field = f"price_{delay.replace('h', 'h').replace('d', 'd')}"

    # Mapping correct des champs
    field_map = {"24h": "price_24h", "7d": "price_7d", "30d": "price_30d"}
    db_field = field_map.get(delay, "price_24h")

    for row in rows:
        asset = row["asset"]
        asset_type = row["asset_type"]
        entry_price = float(row["entry_price"]) if row["entry_price"] else 0
        target_price = float(row["target_price"]) if row["target_price"] else None
        stop_price = float(row["stop_price"]) if row["stop_price"] else None
        perf_id = row["perf_id"]

        # Recupere le prix actuel
        if asset_type == "crypto":
            current_price = await fetch_current_price_crypto(session, asset)
        else:
            current_price = await fetch_current_price_stock(session, asset)

        if current_price is None:
            continue

        # Calcule le resultat
        outcome, return_pct = compute_outcome(
            entry_price, current_price, target_price, stop_price
        )

        # Met a jour la base
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    f"""
                    UPDATE performance
                    SET {db_field} = $1,
                        outcome = $2,
                        return_pct = $3,
                        checked_at = NOW()
                    WHERE id = $4
                    """,
                    current_price,
                    outcome,
                    return_pct,
                    perf_id,
                )
            updated += 1
            print(f"[PERF] {asset} {delay} : ${current_price:.6f} "
                  f"({return_pct:+.2f}%) — {outcome}")

        except Exception as e:
            print(f"[PERF] ERREUR update {asset} : {e}")

        await asyncio.sleep(0.3)

    return updated


# ─── Stats globales ───────────────────────────────────────────────────────────

async def get_performance_stats() -> dict:
    """
    Calcule les statistiques globales de performance du systeme.
    Retourne un dict avec winrate, return moyen, meilleurs/pires signaux.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Stats generales 24h
        stats_24h = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN outcome IN ('hit_target', 'positive') THEN 1 END) as wins,
                COUNT(CASE WHEN outcome IN ('hit_stop', 'negative') THEN 1 END) as losses,
                ROUND(AVG(return_pct)::numeric, 2) as avg_return,
                ROUND(MAX(return_pct)::numeric, 2) as best_return,
                ROUND(MIN(return_pct)::numeric, 2) as worst_return
            FROM performance
            WHERE price_24h IS NOT NULL
        """)

        # Stats par type d'actif
        stats_by_type = await conn.fetch("""
            SELECT
                s.asset_type,
                COUNT(*) as total,
                COUNT(CASE WHEN p.outcome IN ('hit_target', 'positive') THEN 1 END) as wins,
                ROUND(AVG(p.return_pct)::numeric, 2) as avg_return
            FROM performance p
            JOIN signals s ON s.id = p.signal_id
            WHERE p.price_24h IS NOT NULL
            GROUP BY s.asset_type
        """)

        # Top 5 meilleurs signaux
        top_signals = await conn.fetch("""
            SELECT s.asset, s.asset_type, s.score, p.return_pct, p.outcome,
                   s.created_at
            FROM performance p
            JOIN signals s ON s.id = p.signal_id
            WHERE p.return_pct IS NOT NULL
            ORDER BY p.return_pct DESC
            LIMIT 5
        """)

        # 5 pires signaux
        worst_signals = await conn.fetch("""
            SELECT s.asset, s.asset_type, s.score, p.return_pct, p.outcome,
                   s.created_at
            FROM performance p
            JOIN signals s ON s.id = p.signal_id
            WHERE p.return_pct IS NOT NULL
            ORDER BY p.return_pct ASC
            LIMIT 5
        """)

    total = stats_24h["total"] or 0
    wins = stats_24h["wins"] or 0
    winrate = round(wins / total * 100, 1) if total > 0 else 0

    return {
        "total_signals": total,
        "wins": wins,
        "losses": stats_24h["losses"] or 0,
        "winrate_pct": winrate,
        "avg_return_pct": float(stats_24h["avg_return"] or 0),
        "best_return_pct": float(stats_24h["best_return"] or 0),
        "worst_return_pct": float(stats_24h["worst_return"] or 0),
        "by_type": [dict(r) for r in stats_by_type],
        "top_signals": [dict(r) for r in top_signals],
        "worst_signals": [dict(r) for r in worst_signals],
    }


def format_performance_report(stats: dict) -> str:
    """Formate un rapport de performance pour Telegram."""
    total = stats["total_signals"]

    if total == 0:
        return (
            "<b>RAPPORT DE PERFORMANCE</b>\n"
            "- - - - - - - - - - - - -\n"
            "Aucun signal verifie pour l'instant.\n"
            "Les premiers resultats apparaitront 24h apres le premier signal."
        )

    winrate = stats["winrate_pct"]
    avg_return = stats["avg_return_pct"]
    best = stats["best_return_pct"]
    worst = stats["worst_return_pct"]

    winrate_emoji = "🟢" if winrate >= 60 else "🟡" if winrate >= 45 else "🔴"
    avg_emoji = "🟢" if avg_return >= 2 else "🟡" if avg_return >= 0 else "🔴"

    lines = [
        "<b>RAPPORT DE PERFORMANCE</b>",
        "- - - - - - - - - - - - -",
        f"Signaux evalues : {total}",
        f"Winrate : {winrate_emoji} <b>{winrate}%</b> ({stats['wins']}W / {stats['losses']}L)",
        f"Retour moyen 24h : {avg_emoji} <b>{avg_return:+.2f}%</b>",
        f"Meilleur signal : <b>{best:+.2f}%</b>",
        f"Pire signal : <b>{worst:+.2f}%</b>",
    ]

    # Stats par type
    if stats["by_type"]:
        lines.append("\nPar categorie :")
        for t in stats["by_type"]:
            t_winrate = round(t["wins"] / t["total"] * 100) if t["total"] > 0 else 0
            lines.append(
                f"  {t['asset_type'].upper()} : {t_winrate}% winrate "
                f"| {t['avg_return']:+.2f}% moy. ({t['total']} signaux)"
            )

    # Top signaux
    if stats["top_signals"]:
        lines.append("\nMeilleurs signaux :")
        for s in stats["top_signals"][:3]:
            date = s["created_at"].strftime("%d/%m") if s.get("created_at") else ""
            lines.append(
                f"  {s['asset']} {s['return_pct']:+.2f}% "
                f"(score {s['score']}) {date}"
            )

    return "\n".join(lines)


# ─── Boucle de suivi ──────────────────────────────────────────────────────────

async def run_performance_tracker():
    """
    Boucle de suivi des performances.
    Verifie les signaux echus toutes les heures.
    """
    print("[PERF] Tracker de performance demarre.")

    while True:
        try:
            await check_pending_signals()
        except Exception as e:
            print(f"[PERF] ERREUR tracker : {e}")
            await log_system("ERROR", "performance", f"Erreur tracker : {e}")

        # Verification toutes les heures
        await asyncio.sleep(3600)
