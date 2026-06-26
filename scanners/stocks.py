import asyncio
import aiohttp
from datetime import datetime, timezone

from config import Config


# ─── Constantes ───────────────────────────────────────────────────────────────

TWELVE_BASE = "https://api.twelvedata.com"
YAHOO_BASE = "https://query1.finance.yahoo.com"

# Secteurs et leurs ETFs/indices de référence pour découverte dynamique
SECTORS = {
    "IA & Semi-conducteurs": {
        "keywords": ["semiconductor", "artificial intelligence", "chip", "gpu", "data center"],
        "reference_tickers": ["NVDA", "AMD", "INTC", "QCOM", "AVGO", "MU", "AMAT", "ASML.AS", "STM.PA", "IFX.DE"],
    },
    "Biotech & Pharma": {
        "keywords": ["biotech", "pharmaceutical", "drug", "clinical", "therapy"],
        "reference_tickers": ["MRNA", "BNTX", "REGN", "VRTX", "GILD", "SAN.PA", "AZN.L", "NOVN.SW", "BNT.DE"],
    },
    "Cybersécurité": {
        "keywords": ["cybersecurity", "security", "network protection"],
        "reference_tickers": ["CRWD", "PANW", "ZS", "FTNT", "S", "CYBR"],
    },
    "Défense & Spatial": {
        "keywords": ["defense", "aerospace", "space", "military"],
        "reference_tickers": ["LMT", "RTX", "NOC", "BA", "HO.PA", "AIR.PA", "BA.L"],
    },
    "Quantique": {
        "keywords": ["quantum", "computing"],
        "reference_tickers": ["IONQ", "RGTI", "QUBT", "IBM", "GOOGL"],
    },
    "Energie IA": {
        "keywords": ["nuclear", "renewable", "energy", "power"],
        "reference_tickers": ["NEE", "CEG", "VST", "SMR", "NNE", "EDF.PA", "ENGI.PA", "ENEL.MI"],
    },
    "Robotique & Automation": {
        "keywords": ["robotics", "automation", "industrial"],
        "reference_tickers": ["ISRG", "ABB.ST", "FANUY", "ROK", "TER"],
    },
    "Infrastructure IA": {
        "keywords": ["cloud", "data center", "infrastructure"],
        "reference_tickers": ["MSFT", "AMZN", "GOOGL", "META", "DLR", "EQIX"],
    },
    "Stockage & Mémoire": {
        "keywords": ["storage", "memory", "flash", "nand"],
        "reference_tickers": ["WDC", "STX", "MU", "KIOXIA", "SMCI"],
    },
}

# Exchanges Europe pour yfinance
EUROPEAN_SUFFIXES = {
    "Euronext Paris": ".PA",
    "Euronext Amsterdam": ".AS",
    "Euronext Bruxelles": ".BR",
    "Frankfurt Xetra": ".DE",
    "London Stock Exchange": ".L",
    "Milan": ".MI",
}

MIN_MARKET_CAP = 500_000_000   # 500M$ minimum
MIN_VOLUME_USD = 1_000_000     # 1M$ volume journalier minimum


# ─── Twelve Data — Indicateurs techniques ─────────────────────────────────────

async def fetch_technical_indicators(
    session: aiohttp.ClientSession,
    symbol: str,
) -> dict:
    """
    Récupère RSI, MACD et EMA via Twelve Data.
    Retourne un dict vide si erreur ou quota dépassé.
    """
    result = {}

    if not Config.TWELVE_DATA_KEY:
        return result

    # RSI
    try:
        async with session.get(
            f"{TWELVE_BASE}/rsi",
            params={
                "symbol": symbol,
                "interval": "1day",
                "time_period": 14,
                "apikey": Config.TWELVE_DATA_KEY,
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            if "values" in data and data["values"]:
                result["rsi"] = float(data["values"][0]["rsi"])
    except Exception:
        pass

    await asyncio.sleep(0.3)  # Respect rate limit

    # MACD
    try:
        async with session.get(
            f"{TWELVE_BASE}/macd",
            params={
                "symbol": symbol,
                "interval": "1day",
                "apikey": Config.TWELVE_DATA_KEY,
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            if "values" in data and len(data["values"]) >= 2:
                v0 = data["values"][0]
                v1 = data["values"][1]
                macd_now = float(v0["macd"])
                signal_now = float(v0["macd_signal"])
                macd_prev = float(v1["macd"])
                signal_prev = float(v1["macd_signal"])
                result["macd"] = {
                    "macd": macd_now,
                    "signal": signal_now,
                    "histogram": float(v0["macd_hist"]),
                    "crossover_bullish": macd_prev < signal_prev and macd_now > signal_now,
                }
    except Exception:
        pass

    await asyncio.sleep(0.3)

    # EMA 20 et EMA 50
    try:
        async with session.get(
            f"{TWELVE_BASE}/ema",
            params={
                "symbol": symbol,
                "interval": "1day",
                "time_period": 20,
                "apikey": Config.TWELVE_DATA_KEY,
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            if "values" in data and data["values"]:
                result["ema20"] = float(data["values"][0]["ema"])
    except Exception:
        pass

    return result


# ─── Yahoo Finance — Prix et données fondamentales ────────────────────────────

async def fetch_yahoo_quote(
    session: aiohttp.ClientSession,
    symbol: str,
) -> dict | None:
    """
    Récupère le prix, volume, market cap et variation via Yahoo Finance.
    """
    try:
        async with session.get(
            f"{YAHOO_BASE}/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json()

        result_data = data.get("chart", {}).get("result", [])
        if not result_data:
            return None

        meta = result_data[0].get("meta", {})
        quotes = result_data[0].get("indicators", {}).get("quote", [{}])[0]

        closes = quotes.get("close", [])
        volumes = quotes.get("volume", [])

        closes = [c for c in closes if c is not None]
        volumes = [v for v in volumes if v is not None]

        if len(closes) < 2:
            return None

        price = closes[-1]
        prev_close = closes[-2]
        change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
        volume = volumes[-1] if volumes else 0

        market_cap = meta.get("marketCap", 0)
        currency = meta.get("currency", "USD")
        exchange = meta.get("exchangeName", "")

        return {
            "symbol": symbol,
            "price": round(price, 4),
            "prev_close": round(prev_close, 4),
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "market_cap": market_cap,
            "currency": currency,
            "exchange": exchange,
            "closes_5d": closes,
            "avg_volume_5d": sum(volumes) / len(volumes) if volumes else 0,
        }

    except Exception as e:
        return None


# ─── Scoring bourse ───────────────────────────────────────────────────────────

def compute_stock_score(quote: dict, technical: dict) -> tuple[int, list[str]]:
    """
    Calcule le score d'un signal bourse (0-100) et liste les signaux détectés.
    """
    score = 0
    signals = []

    price = quote.get("price", 0)
    change_pct = quote.get("change_pct", 0)
    volume = quote.get("volume", 0)
    avg_volume = quote.get("avg_volume_5d", 0)
    closes = quote.get("closes_5d", [])

    rsi = technical.get("rsi")
    macd = technical.get("macd")
    ema20 = technical.get("ema20")

    # RSI
    if rsi is not None:
        if rsi <= 35:
            score += 25
            signals.append(f"RSI survendu ({rsi:.1f})")
        elif rsi <= 45:
            score += 15
            signals.append(f"RSI bas ({rsi:.1f})")
        elif 50 <= rsi <= 65 and change_pct > 0:
            score += 10
            signals.append(f"RSI momentum haussier ({rsi:.1f})")

    # MACD
    if macd:
        if macd.get("crossover_bullish"):
            score += 25
            signals.append("MACD crossover haussier (journalier)")
        elif macd.get("histogram", 0) > 0 and macd.get("macd", 0) > 0:
            score += 10
            signals.append("MACD positif")

    # Prix au-dessus EMA20
    if ema20 and price:
        if price > ema20 * 1.02:
            score += 15
            signals.append(f"Prix au-dessus EMA20 (${ema20:.2f})")
        elif price > ema20:
            score += 8
            signals.append(f"Prix au-dessus EMA20 (${ema20:.2f})")

    # Volume anormal
    if avg_volume and volume:
        ratio = volume / avg_volume
        if ratio >= 3:
            score += 20
            signals.append(f"Volume institutionnel x{ratio:.1f} vs moyenne 5j")
        elif ratio >= 2:
            score += 12
            signals.append(f"Volume anormal x{ratio:.1f} vs moyenne 5j")

    # Breakout journalier
    if len(closes) >= 5:
        recent_high = max(closes[:-1])
        if price >= recent_high * 0.99:
            score += 15
            signals.append(f"Breakout sur plus haut 5j (${recent_high:.2f})")

    # Momentum journalier fort
    if change_pct >= 5:
        score += 15
        signals.append(f"Hausse journalière forte +{change_pct:.1f}%")
    elif change_pct >= 2:
        score += 8
        signals.append(f"Hausse journalière +{change_pct:.1f}%")

    return min(score, 100), signals


# ─── Analyse d'un symbole bourse ──────────────────────────────────────────────

async def analyze_stock(
    session: aiohttp.ClientSession,
    symbol: str,
    sector: str,
) -> dict | None:
    """
    Analyse complète d'une action : prix Yahoo + indicateurs Twelve Data.
    Retourne un dict si signal qualifié, None sinon.
    """
    quote = await fetch_yahoo_quote(session, symbol)
    if not quote:
        return None

    # Filtre volume minimum
    if quote.get("volume", 0) * quote.get("price", 0) < MIN_VOLUME_USD:
        return None

    technical = await fetch_technical_indicators(session, symbol)

    score, signals = compute_stock_score(quote, technical)

    if score < Config.SIGNAL_SCORE_MIN or len(signals) < Config.SIGNAL_COUNT_MIN:
        return None

    return {
        "symbol": symbol,
        "sector": sector,
        "score": score,
        "signals": signals,
        "quote": quote,
        "technical": technical,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Scanner principal ────────────────────────────────────────────────────────

async def run_stock_scan() -> dict[str, list[dict]]:
    """
    Scan complet US + Europe par secteur.
    Retourne un dict {secteur: [signaux qualifiés]}.
    """
    print("[STOCKS] Démarrage du scan bourse...")
    results_by_sector = {sector: [] for sector in SECTORS}

    async with aiohttp.ClientSession() as session:
        for sector, config in SECTORS.items():
            tickers = config["reference_tickers"]
            print(f"[STOCKS] Scan secteur : {sector} ({len(tickers)} tickers)")

            for symbol in tickers:
                result = await analyze_stock(session, symbol, sector)
                if result:
                    results_by_sector[sector].append(result)
                    print(f"[STOCKS] Signal : {symbol} — {sector} — Score {result['score']}/100")

                await asyncio.sleep(0.5)  # Rate limit Twelve Data

    total = sum(len(v) for v in results_by_sector.values())
    print(f"[STOCKS] Scan terminé. {total} signaux qualifiés sur l'ensemble des secteurs.")

    return results_by_sector
