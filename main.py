import asyncio
import signal
import sys
from datetime import datetime, timezone

from config import Config
from alerts.telegram import test_connections, send_crypto_alert, send_bourse_brief, send_system_alert
from database.db import init_db, test_connection, close_pool, log_system, save_signal
from scanners.crypto import run_crypto_scanner
from scanners.stocks import run_stock_scan
from scanners.clinical import run_clinical_scan
from scanners.government import run_government_scan
from scanners.news import run_news_scan
from analysis.engine import analyze_crypto_signal, format_crypto_alert
from analysis.morning_brief import generate_morning_brief
from tracking.performance import run_performance_tracker, get_performance_stats, format_performance_report


RUNNING = True

# Cache des donnees reglementaires (mis a jour toutes les heures)
_regulatory_cache = {
    "clinical": [],
    "government": {},
    "news": {},
    "last_update": None,
}


def handle_shutdown(sig, frame):
    global RUNNING
    print(f"\n[MAIN] Signal {sig} recu. Arret en cours...")
    RUNNING = False


# ─── Cache reglementaire ──────────────────────────────────────────────────────

async def update_regulatory_cache():
    """Met a jour les donnees reglementaires en cache."""
    global _regulatory_cache
    print("[MAIN] Mise a jour du cache reglementaire...")

    try:
        clinical, government, news = await asyncio.gather(
            run_clinical_scan(),
            run_government_scan(),
            run_news_scan(),
            return_exceptions=True,
        )

        if not isinstance(clinical, Exception):
            _regulatory_cache["clinical"] = clinical
        if not isinstance(government, Exception):
            _regulatory_cache["government"] = government
        if not isinstance(news, Exception):
            _regulatory_cache["news"] = news

        _regulatory_cache["last_update"] = datetime.now(timezone.utc)
        print("[MAIN] Cache reglementaire mis a jour.")
        await log_system("INFO", "regulatory", "Cache mis a jour.")

    except Exception as e:
        print(f"[MAIN] ERREUR cache : {e}")
        await log_system("ERROR", "regulatory", f"Erreur cache : {e}")


async def regulatory_cache_loop():
    """Met a jour le cache reglementaire toutes les heures."""
    while True:
        await update_regulatory_cache()
        await asyncio.sleep(3600)


# ─── Callback crypto ──────────────────────────────────────────────────────────

async def on_crypto_signal(result: dict):
    """Callback pour chaque signal crypto qualifie."""
    symbol = result["symbol"]
    score = result["score"]

    print(f"[MAIN] Signal crypto : {symbol} - Score {score}/100")

    # Enrichissement news crypto
    news_data = _regulatory_cache.get("news", {})
    crypto_news = news_data.get("Crypto", [])
    base = symbol.replace("USDT", "")
    external = [a for a in crypto_news if base.upper() in a.get("title", "").upper()]
    result["external_sources"] = external[:2]

    result = await analyze_crypto_signal(result)
    message = format_crypto_alert(result)
    sent = await send_crypto_alert(message)

    if sent:
        print(f"[MAIN] Alerte crypto envoyee : {symbol}")
    else:
        print(f"[MAIN] ERREUR envoi alerte : {symbol}")

    ai = result.get("ai", {})
    ticker = result.get("ticker", {})

    await save_signal(
        asset=symbol,
        asset_type="crypto",
        score=score,
        signals_detected=result["signals"],
        price=ticker.get("price", 0),
        sources=["Binance Spot"] + [e.get("source", "") for e in external],
        ai_analysis=ai.get("analyse", ""),
        entry_price=ai.get("entree"),
        target_price=ai.get("cible"),
        stop_price=ai.get("stop"),
    )

    await log_system("INFO", "crypto", f"Signal envoye : {symbol} score={score}")


# ─── Morning brief ────────────────────────────────────────────────────────────

async def run_morning_brief():
    """Lance le scan bourse et envoie le morning brief."""
    print("[MAIN] Demarrage du morning brief bourse...")
    await log_system("INFO", "stocks", "Demarrage morning brief.")

    try:
        await update_regulatory_cache()
        results_by_sector = await run_stock_scan()

        clinical_events = _regulatory_cache.get("clinical", [])
        gov_data = _regulatory_cache.get("government", {})
        news_data = _regulatory_cache.get("news", {})

        for sector, signals in results_by_sector.items():
            for result in signals:
                external = []
                external.extend(news_data.get(sector, [])[:2])
                if "Biotech" in sector or "Pharma" in sector:
                    external.extend(clinical_events[:3])
                if "Defense" in sector or "Spatial" in sector:
                    external.extend(gov_data.get("contracts", [])[:2])
                result["external_sources"] = external[:4]

        brief = await generate_morning_brief(results_by_sector)

        # Ajoute le rapport de perf au brief si donnees disponibles
        try:
            stats = await get_performance_stats()
            if stats["total_signals"] > 0:
                perf_report = format_performance_report(stats)
                brief += f"\n\n{perf_report}"
        except Exception:
            pass

        sent = await send_bourse_brief(brief)

        if sent:
            print("[MAIN] Morning brief envoye sur FussBourse.")
            await log_system("INFO", "stocks", "Morning brief envoye.")
        else:
            print("[MAIN] ERREUR envoi morning brief.")

        # Sauvegarde des signaux
        for sector, signals in results_by_sector.items():
            for result in signals:
                ai = result.get("ai", {})
                quote = result.get("quote", {})
                external = result.get("external_sources", [])
                sources = list(set(
                    ["Yahoo Finance", "Twelve Data"]
                    + [e.get("source", "") for e in external if e.get("source")]
                ))
                await save_signal(
                    asset=result["symbol"],
                    asset_type="stock",
                    score=result["score"],
                    signals_detected=result["signals"],
                    price=quote.get("price", 0),
                    sources=sources,
                    ai_analysis=ai.get("catalyseur", ""),
                    entry_price=ai.get("entree"),
                    target_price=ai.get("cible"),
                    stop_price=ai.get("stop"),
                )

    except Exception as e:
        error_msg = f"Erreur morning brief : {e}"
        print(f"[MAIN] {error_msg}")
        await log_system("ERROR", "stocks", error_msg)
        await send_system_alert(f"Erreur morning brief : {error_msg}")


async def schedule_morning_brief():
    """Scheduler du morning brief."""
    while True:
        now = datetime.now(timezone.utc)
        target_hour = Config.MORNING_BRIEF_HOUR_UTC
        target_minute = Config.MORNING_BRIEF_MINUTE_UTC

        seconds_until = (
            (target_hour - now.hour) * 3600
            + (target_minute - now.minute) * 60
            - now.second
        )
        if seconds_until <= 0:
            seconds_until += 86400

        print(
            f"[MAIN] Prochain morning brief dans "
            f"{seconds_until // 3600}h{(seconds_until % 3600) // 60}m "
            f"(objectif : {target_hour:02d}:{target_minute:02d} UTC)"
        )

        await asyncio.sleep(seconds_until)
        await run_morning_brief()


# ─── Demarrage ────────────────────────────────────────────────────────────────

async def startup() -> bool:
    print("=" * 50)
    print("  FUSS MARKET INTELLIGENCE - Demarrage")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 50)

    try:
        Config.validate()
    except EnvironmentError as e:
        print(f"[MAIN] ERREUR CONFIG : {e}")
        return False

    if not await test_connection():
        print("[MAIN] PostgreSQL inaccessible. Arret.")
        return False

    if not await init_db():
        print("[MAIN] Init DB echouee. Arret.")
        return False

    if not await test_connections():
        print("[MAIN] Telegram inaccessible. Arret.")
        return False

    await log_system("INFO", "main", "Systeme demarre avec succes.")
    print("[MAIN] Tous les systemes sont operationnels.")
    return True


# ─── Boucle principale ────────────────────────────────────────────────────────

async def main_loop():
    global RUNNING

    crypto_task = asyncio.create_task(run_crypto_scanner(on_crypto_signal))
    brief_task = asyncio.create_task(schedule_morning_brief())
    regulatory_task = asyncio.create_task(regulatory_cache_loop())
    perf_task = asyncio.create_task(run_performance_tracker())

    all_tasks = {
        "crypto": (crypto_task, lambda: run_crypto_scanner(on_crypto_signal)),
        "brief": (brief_task, schedule_morning_brief),
        "regulatory": (regulatory_task, regulatory_cache_loop),
        "perf": (perf_task, run_performance_tracker),
    }

    heartbeat_count = 0

    while RUNNING:
        await asyncio.sleep(60)
        heartbeat_count += 1

        # Surveillance et redemarrage automatique
        for name, (task, factory) in list(all_tasks.items()):
            if task.done():
                exc = task.exception() if not task.cancelled() else None
                if exc:
                    print(f"[MAIN] Tache {name} arretee : {exc}")
                    await log_system("ERROR", name, str(exc))
                new_task = asyncio.create_task(factory())
                all_tasks[name] = (new_task, factory)
                print(f"[MAIN] Tache {name} redemarre.")

        # Heartbeat toutes les heures
        if heartbeat_count % 60 == 0:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            print(f"[MAIN] Heartbeat - {ts}")
            await log_system("INFO", "main", f"Heartbeat - {ts}")

    for name, (task, _) in all_tasks.items():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ─── Shutdown ─────────────────────────────────────────────────────────────────

async def shutdown():
    print("[MAIN] Fermeture des connexions...")
    await log_system("INFO", "main", "Arret du systeme.")
    await close_pool()
    print("[MAIN] Arret termine.")


async def run():
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    if not await startup():
        print("[MAIN] Demarrage echoue. Extinction.")
        sys.exit(1)

    try:
        await main_loop()
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(run())
