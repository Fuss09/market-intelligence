import asyncio
import signal
import sys
from datetime import datetime, timezone

from config import Config
from alerts.telegram import test_connections, send_system_alert
from database.db import init_db, test_connection, close_pool, log_system


RUNNING = True


def handle_shutdown(sig, frame):
    """Gère l'arrêt propre du système sur signal SIGTERM / SIGINT."""
    global RUNNING
    print(f"\n[MAIN] Signal {sig} reçu. Arrêt en cours...")
    RUNNING = False


async def startup() -> bool:
    """
    Séquence de démarrage : valide config, DB, Telegram.
    Retourne True si tout est opérationnel.
    """
    print("=" * 50)
    print("  FUSS MARKET INTELLIGENCE — Démarrage")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 50)

    # 1. Validation de la configuration
    try:
        Config.validate()
    except EnvironmentError as e:
        print(f"[MAIN] ERREUR CONFIG : {e}")
        return False

    # 2. Test PostgreSQL
    db_ok = await test_connection()
    if not db_ok:
        print("[MAIN] Impossible de se connecter à PostgreSQL. Arrêt.")
        return False

    # 3. Initialisation des tables
    db_init = await init_db()
    if not db_init:
        print("[MAIN] Échec de l'initialisation des tables. Arrêt.")
        return False

    # 4. Test Telegram
    tg_ok = await test_connections()
    if not tg_ok:
        print("[MAIN] Impossible d'envoyer sur Telegram. Arrêt.")
        return False

    # 5. Log démarrage en base
    await log_system("INFO", "main", "Système démarré avec succès.")

    print("[MAIN] Tous les systèmes sont opérationnels.")
    return True


async def main_loop():
    """
    Boucle principale du système.
    Module 1 : boucle minimale avec heartbeat toutes les 60 secondes.
    Les scanners seront branchés ici dans les modules suivants.
    """
    global RUNNING
    heartbeat_count = 0

    while RUNNING:
        await asyncio.sleep(60)
        heartbeat_count += 1

        # Log heartbeat toutes les heures (60 cycles x 60s)
        if heartbeat_count % 60 == 0:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            print(f"[MAIN] Heartbeat — {ts}")
            await log_system("INFO", "main", f"Heartbeat — {ts}")


async def shutdown():
    """Séquence d'arrêt propre."""
    print("[MAIN] Fermeture des connexions...")
    await log_system("INFO", "main", "Arrêt du système.")
    await close_pool()
    print("[MAIN] Arrêt terminé.")


async def run():
    """Point d'entrée principal."""
    # Gestion des signaux système pour arrêt propre
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    ok = await startup()
    if not ok:
        print("[MAIN] Démarrage échoué. Extinction.")
        sys.exit(1)

    try:
        await main_loop()
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(run())
