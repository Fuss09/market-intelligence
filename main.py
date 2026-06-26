import asyncio
import signal
import sys
from datetime import datetime, timezone

from config import Config
from alerts.telegram import test_connections, send_crypto_alert, send_system_alert
from database.db import init_db, test_connection, close_pool, log_system, save_signal
from scanners.crypto import run_crypto_scanner
from analysis.engine import analyze_crypto_signal, format_crypto_alert


RUNNING = True


def handle_shutdown(sig, frame):
    global RUNNING
    print(f"\n[MAIN] Signal {sig} reçu. Arrêt en cours...")
    RUNNING = False


async def on_crypto_signal(result: dict):
    """
    Callback appelé par le scanner crypto pour chaque signal qualifié.
    1. Analyse IA
    2. Formate et envoie l'alerte Telegram
    3. Sauvegarde en base
    """
    symbol = result["symbol"]
    score = result["score"]

    print(f"[MAIN] Signal qualifié : {symbol} — Score {score}/100")

    # 1. Enrichissement IA
    result = await analyze_crypto_signal(result)

    # 2. Formatage et envoi Telegram
    message = format_crypto_alert(result)
    sent = await send_crypto_alert(message)

    if sent:
        print(f"[MAIN] Alerte envoyée : {symbol}")
    else:
        print(f"[MAIN] ERREUR envoi alerte : {symbol}")

    # 3. Sauvegarde en base
    ai = result.get("ai", {})
    ticker = result.get("ticker", {})

    await save_signal(
        asset=symbol,
        asset_type="crypto",
        score=score,
        signals_detected=result["signals"],
        price=ticker.get("price", 0),
        sources=["Binance Spot"],
        ai_analysis=ai.get("analyse", ""),
        entry_price=ai.get("entree"),
        target_price=ai.get("cible"),
        stop_price=ai.get("stop"),
    )

    await log_system("INFO", "crypto", f"Signal envoyé : {symbol} score={score}")


async def startup() -> bool:
    print("=" * 50)
    print("  FUSS MARKET INTELLIGENCE — Démarrage")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 50)

    try:
        Config.validate()
    except EnvironmentError as e:
        print(f"[MAIN] ERREUR CONFIG : {e}")
        return False

    db_ok = await test_connection()
    if not db_ok:
        print("[MAIN] Impossible de se connecter à PostgreSQL. Arrêt.")
        return False

    db_init = await init_db()
    if not db_init:
        print("[MAIN] Échec de l'initialisation des tables. Arrêt.")
        return False

    tg_ok = await test_connections()
    if not tg_ok:
        print("[MAIN] Impossible d'envoyer sur Telegram. Arrêt.")
        return False

    await log_system("INFO", "main", "Système démarré avec succès.")
    print("[MAIN] Tous les systèmes sont opérationnels.")
    return True


async def main_loop():
    """
    Boucle principale.
    Lance le scanner crypto en tâche de fond.
    """
    global RUNNING

    # Lance le scanner crypto
    crypto_task = asyncio.create_task(
        run_crypto_scanner(on_crypto_signal)
    )

    heartbeat_count = 0

    while RUNNING:
        await asyncio.sleep(60)
        heartbeat_count += 1

        # Vérifie que la tâche crypto tourne toujours
        if crypto_task.done():
            exc = crypto_task.exception()
            if exc:
                print(f"[MAIN] Scanner crypto arrêté avec erreur : {exc}")
                await log_system("ERROR", "crypto", f"Scanner arrêté : {exc}")
                await send_system_alert(f"⚠️ Scanner crypto arrêté : {exc}\nRedémarrage...")
            # Relance le scanner
            crypto_task = asyncio.create_task(
                run_crypto_scanner(on_crypto_signal)
            )
            print("[MAIN] Scanner crypto redémarré.")

        # Heartbeat toutes les heures
        if heartbeat_count % 60 == 0:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            print(f"[MAIN] Heartbeat — {ts}")
            await log_system("INFO", "main", f"Heartbeat — {ts}")

    # Arrêt propre
    crypto_task.cancel()
    try:
        await crypto_task
    except asyncio.CancelledError:
        pass


async def shutdown():
    print("[MAIN] Fermeture des connexions...")
    await log_system("INFO", "main", "Arrêt du système.")
    await close_pool()
    print("[MAIN] Arrêt terminé.")


async def run():
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
