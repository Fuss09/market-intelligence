import asyncio
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import aiohttp

from config import Config


# ─── Constantes ───────────────────────────────────────────────────────────────

BINANCE_REST = "https://api.binance.com"
BINANCE_WS_BASE = "wss://stream.binance.com:9443"
KLINE_INTERVAL = "1h"
KLINE_LIMIT = 100          # bougies pour calcul indicateurs
MIN_VOLUME_USDT = 500_000  # volume 24h minimum pour filtrer le bruit
SCAN_INTERVAL = 300        # rescan complet toutes les 5 minutes


# ─── Stockage en mémoire ──────────────────────────────────────────────────────

# Pour chaque symbole : deque de bougies 1h (OHLCV)
_candles: dict[str, deque] = defaultdict(lambda: deque(maxlen=KLINE_LIMIT))

# Dernière alerte envoyée par symbole (timestamp) — anti-spam
_last_alert: dict[str, float] = {}

# Cache des tickers 24h
_tickers: dict[str, dict] = {}


# ─── Récupération des données ─────────────────────────────────────────────────

async def fetch_all_usdt_symbols(session: aiohttp.ClientSession) -> list[str]:
    """Récupère toutes les paires USDT Spot actives sur Binance."""
    try:
        async with session.get(
            f"{BINANCE_REST}/api/v3/exchangeInfo",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()

        symbols = [
            s["symbol"]
            for s in data["symbols"]
            if s["quoteAsset"] == "USDT"
            and s["status"] == "TRADING"
            and s["isSpotTradingAllowed"]
        ]
        print(f"[CRYPTO] {len(symbols)} paires USDT actives détectées.")
        return symbols

    except Exception as e:
        print(f"[CRYPTO] ERREUR fetch_all_usdt_symbols : {e}")
        return []


async def fetch_tickers_24h(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Récupère les tickers 24h pour toutes les paires (volume, variation, prix)."""
    try:
        async with session.get(
            f"{BINANCE_REST}/api/v3/ticker/24hr",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()

        tickers = {}
        for t in data:
            if t["symbol"].endswith("USDT"):
                tickers[t["symbol"]] = {
                    "price": float(t["lastPrice"]),
                    "change_pct": float(t["priceChangePercent"]),
                    "volume_usdt": float(t["quoteVolume"]),
                    "high_24h": float(t["highPrice"]),
                    "low_24h": float(t["lowPrice"]),
                    "volume_base": float(t["volume"]),
                }
        return tickers

    except Exception as e:
        print(f"[CRYPTO] ERREUR fetch_tickers_24h : {e}")
        return {}


async def fetch_klines(
    session: aiohttp.ClientSession, symbol: str
) -> list[dict]:
    """Récupère les bougies 1h pour un symbole."""
    try:
        async with session.get(
            f"{BINANCE_REST}/api/v3/klines",
            params={"symbol": symbol, "interval": KLINE_INTERVAL, "limit": KLINE_LIMIT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            raw = await resp.json()

        candles = []
        for k in raw:
            candles.append({
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
            })
        return candles

    except Exception as e:
        print(f"[CRYPTO] ERREUR fetch_klines {symbol} : {e}")
        return []


# ─── Calcul des indicateurs ───────────────────────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """Calcule le RSI sur une liste de closes."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_ema(closes: list[float], period: int) -> list[float]:
    """Calcule l'EMA sur une liste de closes."""
    if len(closes) < period:
        return []

    k = 2 / (period + 1)
    ema = [sum(closes[:period]) / period]

    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))

    return ema


def calc_macd(closes: list[float]) -> dict | None:
    """Calcule MACD (12/26/9)."""
    if len(closes) < 35:
        return None

    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)

    if not ema12 or not ema26:
        return None

    # Aligne les deux EMA
    diff = len(ema12) - len(ema26)
    ema12 = ema12[diff:]

    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]

    if len(macd_line) < 9:
        return None

    signal_line = calc_ema(macd_line, 9)
    if not signal_line:
        return None

    macd_val = macd_line[-1]
    signal_val = signal_line[-1]
    histogram = macd_val - signal_val

    # Crossover : MACD passe au-dessus du signal
    crossover = (
        len(macd_line) >= 2
        and len(signal_line) >= 2
        and macd_line[-2] < signal_line[-2]
        and macd_line[-1] > signal_line[-1]
    )

    return {
        "macd": round(macd_val, 8),
        "signal": round(signal_val, 8),
        "histogram": round(histogram, 8),
        "crossover_bullish": crossover,
    }


def calc_atr(candles: list[dict], period: int = 14) -> float | None:
    """Calcule l'ATR (Average True Range)."""
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    return round(sum(trs[-period:]) / period, 8)


def calc_volume_spike(candles: list[dict]) -> dict | None:
    """Détecte un spike de volume vs moyenne des 20 dernières bougies."""
    if len(candles) < 21:
        return None

    recent_volumes = [c["quote_volume"] for c in candles[-21:-1]]
    avg_volume = sum(recent_volumes) / len(recent_volumes)
    current_volume = candles[-1]["quote_volume"]

    if avg_volume == 0:
        return None

    ratio = current_volume / avg_volume

    return {
        "current": round(current_volume, 2),
        "avg_20h": round(avg_volume, 2),
        "ratio": round(ratio, 2),
        "is_spike": ratio >= 2.0,
    }


def calc_support_resistance(candles: list[dict]) -> dict:
    """Calcule support et résistance simples sur les 50 dernières bougies."""
    if len(candles) < 10:
        return {}

    recent = candles[-50:] if len(candles) >= 50 else candles
    highs = [c["high"] for c in recent]
    lows = [c["low"] for c in recent]
    current = candles[-1]["close"]

    resistance = max(highs)
    support = min(lows)

    breakout_up = current >= resistance * 0.99
    breakdown = current <= support * 1.01

    return {
        "support": round(support, 8),
        "resistance": round(resistance, 8),
        "breakout_up": breakout_up,
        "breakdown": breakdown,
        "pct_from_resistance": round((resistance - current) / current * 100, 2),
        "pct_from_support": round((current - support) / current * 100, 2),
    }


def calc_momentum(candles: list[dict]) -> dict:
    """Calcule le momentum 1h et 6h."""
    result = {}

    if len(candles) >= 2:
        m1h = (candles[-1]["close"] - candles[-2]["close"]) / candles[-2]["close"] * 100
        result["momentum_1h"] = round(m1h, 3)

    if len(candles) >= 7:
        m6h = (candles[-1]["close"] - candles[-7]["close"]) / candles[-7]["close"] * 100
        result["momentum_6h"] = round(m6h, 3)

    return result


# ─── Scoring ──────────────────────────────────────────────────────────────────

def compute_score(indicators: dict, ticker: dict) -> tuple[int, list[str]]:
    """
    Calcule le score d'un signal (0-100) et liste les signaux détectés.
    Score minimum 75 et minimum 3 signaux pour déclencher une alerte.
    """
    score = 0
    signals = []

    rsi = indicators.get("rsi")
    macd = indicators.get("macd")
    volume = indicators.get("volume_spike")
    sr = indicators.get("support_resistance")
    momentum = indicators.get("momentum")
    atr = indicators.get("atr")
    change_pct = ticker.get("change_pct", 0)

    # RSI survendu (opportunité d'achat)
    if rsi is not None:
        if rsi <= 30:
            score += 25
            signals.append(f"RSI survendu ({rsi})")
        elif rsi <= 40:
            score += 15
            signals.append(f"RSI bas ({rsi})")
        elif rsi >= 60 and change_pct > 0:
            score += 10
            signals.append(f"RSI momentum haussier ({rsi})")

    # MACD
    if macd:
        if macd["crossover_bullish"]:
            score += 25
            signals.append("MACD crossover haussier")
        elif macd["histogram"] > 0 and macd["macd"] > 0:
            score += 10
            signals.append("MACD positif")

    # Volume spike
    if volume and volume["is_spike"]:
        ratio = volume["ratio"]
        if ratio >= 5:
            score += 25
            signals.append(f"Volume spike x{ratio:.1f} vs moyenne")
        elif ratio >= 3:
            score += 18
            signals.append(f"Volume spike x{ratio:.1f} vs moyenne")
        else:
            score += 12
            signals.append(f"Volume spike x{ratio:.1f} vs moyenne")

    # Breakout résistance
    if sr:
        if sr.get("breakout_up"):
            score += 20
            signals.append(f"Cassure résistance (${sr['resistance']:.6f})")
        elif sr.get("pct_from_resistance", 100) <= 2:
            score += 8
            signals.append(f"Approche résistance ({sr['pct_from_resistance']:.1f}%)")

    # Momentum
    if momentum:
        m1h = momentum.get("momentum_1h", 0)
        m6h = momentum.get("momentum_6h", 0)
        if m1h > 3 and m6h > 5:
            score += 15
            signals.append(f"Momentum fort 1h +{m1h:.1f}% / 6h +{m6h:.1f}%")
        elif m1h > 1.5:
            score += 8
            signals.append(f"Momentum 1h +{m1h:.1f}%")

    # Variation 24h forte avec volume
    if change_pct >= 10 and volume and volume["ratio"] >= 2:
        score += 10
        signals.append(f"Variation 24h +{change_pct:.1f}% avec volume")

    return min(score, 100), signals


# ─── Analyse d'un symbole ─────────────────────────────────────────────────────

async def analyze_symbol(
    session: aiohttp.ClientSession,
    symbol: str,
    ticker: dict,
) -> dict | None:
    """
    Récupère les klines, calcule les indicateurs et le score.
    Retourne un dict si le signal est qualifié (score >= 75, signaux >= 3).
    """
    candles = await fetch_klines(session, symbol)
    if len(candles) < 30:
        return None

    closes = [c["close"] for c in candles]

    indicators = {
        "rsi": calc_rsi(closes),
        "macd": calc_macd(closes),
        "volume_spike": calc_volume_spike(candles),
        "support_resistance": calc_support_resistance(candles),
        "momentum": calc_momentum(candles),
        "atr": calc_atr(candles),
    }

    score, signals = compute_score(indicators, ticker)

    if score < Config.SIGNAL_SCORE_MIN or len(signals) < Config.SIGNAL_COUNT_MIN:
        return None

    return {
        "symbol": symbol,
        "score": score,
        "signals": signals,
        "indicators": indicators,
        "ticker": ticker,
        "candles_last": candles[-3:],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Boucle principale du scanner ─────────────────────────────────────────────

async def run_crypto_scanner(signal_callback):
    """
    Boucle principale du scanner crypto.
    signal_callback(result) est appelé pour chaque signal qualifié.
    """
    print("[CRYPTO] Démarrage du scanner crypto...")

    headers = {}
    if Config.BINANCE_API_KEY:
        headers["X-MBX-APIKEY"] = Config.BINANCE_API_KEY

    while True:
        try:
            scan_start = time.time()
            print(f"[CRYPTO] Début du scan — {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

            async with aiohttp.ClientSession(headers=headers) as session:
                # 1. Récupère tous les symboles USDT
                symbols = await fetch_all_usdt_symbols(session)
                if not symbols:
                    await asyncio.sleep(60)
                    continue

                # 2. Récupère les tickers 24h pour filtrer par volume
                tickers = await fetch_tickers_24h(session)

                # 3. Filtre par volume minimum
                eligible = [
                    s for s in symbols
                    if tickers.get(s, {}).get("volume_usdt", 0) >= MIN_VOLUME_USDT
                ]
                print(f"[CRYPTO] {len(eligible)} paires avec volume >= ${MIN_VOLUME_USDT:,.0f}")

                # 4. Analyse par batch de 10 (évite le rate limit Binance)
                qualified = []
                batch_size = 10

                for i in range(0, len(eligible), batch_size):
                    batch = eligible[i:i + batch_size]
                    tasks = [
                        analyze_symbol(session, sym, tickers.get(sym, {}))
                        for sym in batch
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for result in results:
                        if isinstance(result, dict):
                            qualified.append(result)

                    # Pause entre les batches pour respecter le rate limit
                    await asyncio.sleep(0.5)

                print(f"[CRYPTO] {len(qualified)} signaux qualifiés détectés.")

                # 5. Envoie les signaux qualifiés via callback
                for result in sorted(qualified, key=lambda x: x["score"], reverse=True):
                    sym = result["symbol"]
                    now = time.time()

                    # Anti-spam : 1 alerte max par paire par scan
                    if now - _last_alert.get(sym, 0) > SCAN_INTERVAL:
                        _last_alert[sym] = now
                        await signal_callback(result)

            elapsed = round(time.time() - scan_start, 1)
            print(f"[CRYPTO] Scan terminé en {elapsed}s. Prochain scan dans {SCAN_INTERVAL}s.")

        except Exception as e:
            print(f"[CRYPTO] ERREUR scan complet : {e}")

        await asyncio.sleep(SCAN_INTERVAL)
