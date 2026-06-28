import aiohttp
import asyncio
from datetime import datetime, timezone


# ─── Constantes ───────────────────────────────────────────────────────────────

BINANCE_REST = "https://api.binance.com"
YAHOO_BASE = "https://query1.finance.yahoo.com"

# Seuils de penalite macro
BTC_BEARISH_THRESHOLD = -3.0     # BTC baisse > 3% sur 24h = marche crypto bearish
BTC_STRONG_BEAR = -6.0           # BTC baisse > 6% = marche crypto tres bearish
SPY_BEARISH_THRESHOLD = -1.5     # SPY baisse > 1.5% = marche US bearish
SPY_STRONG_BEAR = -3.0           # SPY baisse > 3% = marche US tres bearish
CAC_BEARISH_THRESHOLD = -1.5     # CAC40 baisse > 1.5% = marche EU bearish


# ─── Donnees macro ────────────────────────────────────────────────────────────

async def fetch_btc_trend(session: aiohttp.ClientSession) -> dict:
    """Recupere la tendance BTC sur 24h via Binance."""
    try:
        async with session.get(
            f"{BINANCE_REST}/api/v3/ticker/24hr",
            params={"symbol": "BTCUSDT"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()

        change_pct = float(data.get("priceChangePercent", 0))
        price = float(data.get("lastPrice", 0))
        volume = float(data.get("quoteVolume", 0))

        return {
            "symbol": "BTC",
            "price": price,
            "change_24h": change_pct,
            "volume_24h": volume,
        }

    except Exception as e:
        print(f"[MACRO] ERREUR BTC trend : {e}")
        return {}


async def fetch_index_trend(
    session: aiohttp.ClientSession,
    symbol: str,
    name: str,
) -> dict:
    """Recupere la tendance d'un indice boursier via Yahoo Finance."""
    try:
        async with session.get(
            f"{YAHOO_BASE}/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "2d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return {}

        quotes = result[0].get("indicators", {}).get("quote", [{}])[0]
        closes = [c for c in quotes.get("close", []) if c is not None]

        if len(closes) < 2:
            return {}

        change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100

        return {
            "symbol": symbol,
            "name": name,
            "price": round(closes[-1], 2),
            "change_24h": round(change_pct, 2),
        }

    except Exception as e:
        print(f"[MACRO] ERREUR {name} trend : {e}")
        return {}


# ─── Analyse macro ────────────────────────────────────────────────────────────

async def get_macro_context() -> dict:
    """
    Recupere le contexte macro global :
    BTC, SPY (US), CAC40 (Europe), VIX (volatilite).
    """
    async with aiohttp.ClientSession() as session:
        btc, spy, cac, vix = await asyncio.gather(
            fetch_btc_trend(session),
            fetch_index_trend(session, "SPY", "S&P500"),
            fetch_index_trend(session, "^FCHI", "CAC40"),
            fetch_index_trend(session, "^VIX", "VIX"),
        )

    macro = {
        "btc": btc,
        "spy": spy,
        "cac": cac,
        "vix": vix,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Calcule le regime de marche global
    macro["regime_crypto"] = _get_crypto_regime(btc)
    macro["regime_stocks"] = _get_stock_regime(spy, cac, vix)

    return macro


def _get_crypto_regime(btc: dict) -> str:
    """
    Regime du marche crypto base sur BTC.
    BULLISH / NEUTRAL / BEARISH / STRONG_BEAR
    """
    if not btc:
        return "NEUTRAL"

    change = btc.get("change_24h", 0)

    if change <= BTC_STRONG_BEAR:
        return "STRONG_BEAR"
    if change <= BTC_BEARISH_THRESHOLD:
        return "BEARISH"
    if change >= 3.0:
        return "BULLISH"
    return "NEUTRAL"


def _get_stock_regime(spy: dict, cac: dict, vix: dict) -> str:
    """
    Regime du marche actions base sur SPY + CAC40 + VIX.
    BULLISH / NEUTRAL / BEARISH / STRONG_BEAR
    """
    if not spy and not cac:
        return "NEUTRAL"

    spy_change = spy.get("change_24h", 0) if spy else 0
    cac_change = cac.get("change_24h", 0) if cac else 0
    vix_level = vix.get("price", 20) if vix else 20

    # VIX > 30 = peur elevee sur le marche
    fear = vix_level > 30

    avg_change = (spy_change + cac_change) / 2 if (spy and cac) else (spy_change or cac_change)

    if avg_change <= SPY_STRONG_BEAR or (avg_change <= SPY_BEARISH_THRESHOLD and fear):
        return "STRONG_BEAR"
    if avg_change <= SPY_BEARISH_THRESHOLD:
        return "BEARISH"
    if avg_change >= 1.0 and not fear:
        return "BULLISH"
    return "NEUTRAL"


# ─── Ajustement du score ──────────────────────────────────────────────────────

def apply_macro_filter(
    score: int,
    signals: list[str],
    asset_type: str,
    macro: dict,
) -> tuple[int, list[str]]:
    """
    Applique le filtre macro sur un score de signal.
    Retourne (score_ajuste, signals_mis_a_jour).

    Penalites :
    - BEARISH : -10 points
    - STRONG_BEAR : -25 points + signal bloquant si score final < 75
    """
    if not macro:
        return score, signals

    signals = list(signals)

    if asset_type == "crypto":
        regime = macro.get("regime_crypto", "NEUTRAL")
        btc = macro.get("btc", {})
        btc_change = btc.get("change_24h", 0)

        if regime == "STRONG_BEAR":
            score -= 25
            signals.append(f"FILTRE MACRO : BTC {btc_change:+.1f}% - Marche tres bearish")
        elif regime == "BEARISH":
            score -= 10
            signals.append(f"FILTRE MACRO : BTC {btc_change:+.1f}% - Marche bearish")
        elif regime == "BULLISH":
            score += 5
            signals.append(f"FILTRE MACRO : BTC {btc_change:+.1f}% - Marche haussier")

    else:  # stocks
        regime = macro.get("regime_stocks", "NEUTRAL")
        spy = macro.get("spy", {})
        spy_change = spy.get("change_24h", 0)
        vix = macro.get("vix", {})
        vix_level = vix.get("price", 20)

        if regime == "STRONG_BEAR":
            score -= 25
            signals.append(
                f"FILTRE MACRO : SPY {spy_change:+.1f}% VIX {vix_level:.0f} - Marche tres bearish"
            )
        elif regime == "BEARISH":
            score -= 10
            signals.append(f"FILTRE MACRO : SPY {spy_change:+.1f}% - Marche bearish")
        elif regime == "BULLISH":
            score += 5
            signals.append(f"FILTRE MACRO : SPY {spy_change:+.1f}% - Marche haussier")

    return min(max(score, 0), 100), signals


def get_macro_summary(macro: dict) -> str:
    """Formate un resume macro pour le morning brief."""
    if not macro:
        return "Donnees macro non disponibles."

    btc = macro.get("btc", {})
    spy = macro.get("spy", {})
    cac = macro.get("cac", {})
    vix = macro.get("vix", {})
    regime_crypto = macro.get("regime_crypto", "NEUTRAL")
    regime_stocks = macro.get("regime_stocks", "NEUTRAL")

    regime_emoji = {
        "BULLISH": "🟢",
        "NEUTRAL": "🟡",
        "BEARISH": "🔴",
        "STRONG_BEAR": "⛔",
    }

    lines = []

    if btc:
        lines.append(
            f"BTC {btc.get('change_24h', 0):+.1f}% "
            f"| Regime crypto : {regime_emoji.get(regime_crypto, '🟡')} {regime_crypto}"
        )
    if spy:
        lines.append(f"SPY {spy.get('change_24h', 0):+.1f}%", )
    if cac:
        lines[-1] += f" | CAC40 {cac.get('change_24h', 0):+.1f}%"
    if vix:
        lines.append(
            f"VIX {vix.get('price', 0):.1f} "
            f"| Regime actions : {regime_emoji.get(regime_stocks, '🟡')} {regime_stocks}"
        )

    return "\n".join(lines) if lines else "Donnees macro non disponibles."
