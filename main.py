import asyncio
import signal
import sys
from datetime import datetime, timezone

from config import Config
from alerts.telegram import test_connections, send_crypto_alert, send_bourse_brief, send_system_alert
from database.db import init_db, test_connection, close_pool, log_system, save_signal
from scanners.crypto import run_crypto_scanner
from scanners.stocks import run_stock_scan
from analysis.engine import analyze_crypto_signal, format_crypto_alert
from analysis.morning_brief import generate_morning_brief


RUNNING = True


def handle_shutdown(sig, frame):
    global RUNNING
    print(f"\n[MAIN] Signal {sig} reçu. Arrêt en cours...")
    RUNNING = False


# ─── Callback crypto ──────────────────────────────────────────────────────────

async def on_crypto_signal(result: dict):
    """
    Callback appelé par le scanner crypto pour chaque signal qualifié.
    """
    symbol = result["symbol"]
    score = result["score"]

    print(f"[MAIN] Signal crypto : {symbol} — Score {score}/100")

    result = await analyze_crypto_signal(result)
    message = format_crypto_alert(result)
    sent = await send_crypto_alert(message)

    if sent:
        print(f"[MAIN] Alerte crypto envoyée : {symbol}")
    else:
        print(f"[MAIN] ERREUR envoi alerte crypto : {symbol}")

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


# ─── Morning brief bourse ─────────────────────────────────────────────────────

async def run_morning_brief():
    """
    Lance le scan bourse et génère le morning brief.
    Appelé automatiquement à l'heure configurée (MORNING_BRIEF_HOUR_UTC).
    """
    print("[MAIN] Démarrage du morning brief bourse...")
    await log_system("INFO", "stocks", "Démarrage morning brief.")

    try:
        results_by_sector = await run_stock_scan()
        brief = await generate_morning_brief(results_by_sector)
        sent = await send_bourse_brief(brief)

        if sent:
            print("[MAIN] Morning brief envoyé sur FussBourse.")
            await log_system("INFO", "stocks", "Morning brief envoyé.")
        else:
            print("[MAIN] ERREUR envoi morning brief.")
            await log_system("ERROR", "stocks", "Échec envoi morning brief.")

        # Sauvegarde des signaux bourse en base
        for sector, signals in results_by_sector.items():
            for result in signals:
                ai = result.get("ai", {})
                quote = result.get("quote", {})
                await save_signal(
                    asset=result["symbol"],
                    asset_type="stock",
                    score=result["score"],
                    signals_detected=result["signals"],
                    price=quote.get("price", 0),
                    sources=["Yahoo Finance", "Twelve Data"],
                    ai_analysis=ai.get("catalyseur", ""),
                    entry_price=ai.get("entree"),
                    target_price=ai.get("cible"),
                    stop_price=ai.get("stop"),
                )

    except Exception as e:
        error_msg = f"Erreur morning brief : {e}"
        print(f"[MAIN] {error_msg}")
        await log_system("ERROR", "stocks", error_msg)
        await send_system_alert(f"⚠️ {error_msg}")


async def schedule_morning_brief():
    """
    Scheduler du morning brief.
    Attend l'heure cible (MORNING_BRIEF_HOUR_UTC:MORNING_BRIEF_MINUTE_UTC)
    puis lance le brief chaque jour.
    """
    while True:
        now = datetime.now(timezone.utc)
        target_hour = Config.MORNING_BRIEF_HOUR_UTC
        target_minute = Config.MORNING_BRIEF_MINUTE_UTC

        # Calcule les secondes jusqu'au prochain déclenchement
        seconds_until = (
            (target_hour - now.hour) * 3600
            + (target_minute - now.minute) * 60
            - now.second
        )

        if seconds_until <= 0:
            seconds_until += 86400  # Demain à la même heure

        next_run = datetime.now(timezone.utc)
        print(
            f"[MAIN] Prochain morning brief dans "
            f"{seconds_until // 3600}h{(seconds_until % 3600) // 60}m "
            f"(objectif : {target_hour:02d}:{target_minute:02d} UTC)"
        )

        await asyncio.sleep(seconds_until)
        await run_morning_brief()


# ─── Démarrage ────────────────────────────────────────────────────────────────

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


# ─── Boucle principale ────────────────────────────────────────────────────────

async def main_loop():
    global RUNNING

    # Lance le scanner crypto en continu
    crypto_task = asyncio.create_task(run_crypto_scanner(on_crypto_signal))

    # Lance le scheduler du morning brief
    brief_task = asyncio.create_task(schedule_morning_brief())

    heartbeat_count = 0

    while RUNNING:
        await asyncio.sleep(60)
        heartbeat_count += 1

        # Surveillance et redémarrage automatique du scanner crypto
        if crypto_task.done():
            exc = crypto_task.exception() if not crypto_task.cancelled() else None
            if exc:
                print(f"[MAIN] Scanner crypto arrêté : {exc}")
                await log_system("ERROR", "crypto", f"Scanner arrêté : {exc}")
                await send_system_alert(f"⚠️ Scanner crypto arrêté. Redémarrage...")
            crypto_task = asyncio.create_task(run_crypto_scanner(on_crypto_signal))
            print("[MAIN] Scanner crypto redémarré.")

        # Surveillance scheduler morning brief
        if brief_task.done():
            exc = brief_task.exception() if not brief_task.cancelled() else None
            if exc:
                print(f"[MAIN] Scheduler morning brief arrêté : {exc}")
                await log_system("ERROR", "stocks", f"Scheduler arrêté : {exc}")
            brief_task = asyncio.create_task(schedule_morning_brief())
            print("[MAIN] Scheduler morning brief redémarré.")

        # Heartbeat toutes les heures
        if heartbeat_count % 60 == 0:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            print(f"[MAIN] Heartbeat — {ts}")
            await log_system("INFO", "main", f"Heartbeat — {ts}")

    # Arrêt propre
    for task in [crypto_task, brief_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ─── Shutdown ─────────────────────────────────────────────────────────────────

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
