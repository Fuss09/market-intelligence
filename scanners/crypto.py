import asyncio
import aiohttp
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from config import Config
from analysis.macro import get_macro_context, apply_macro_filter

# ─── Constantes ───────────────────────────────────────────────────────────────

BINANCE_REST = "https://api.binance.com”
KLINE_INTERVAL = "1h”
KLINE_LIMIT = 100
MIN_VOLUME_USDT = 500_000
SCAN_INTERVAL = 300

_macro_cache = {}
_last_alert: dict[str, float] = {}

# ─── Fetch donnees ────────────────────────────────────────────────────────────

async def fetch_all_usdt_symbols(session: aiohttp.ClientSession) -> list[str]:
try:
async with session.get(
f”{BINANCE_REST}/api/v3/exchangeInfo”,
timeout=aiohttp.ClientTimeout(total=30),
) as resp:
data = await resp.json()
return [
s["symbol”] for s in data["symbols”]
if s["quoteAsset”] == "USDT”
and s["status”] == "TRADING”
and s["isSpotTradingAllowed”]
]
except Exception as e:
print(f”[CRYPTO] ERREUR fetch_all_usdt_symbols : {e}”)
return []

async def fetch_tickers_24h(session: aiohttp.ClientSession) -> dict[str, dict]:
try:
async with session.get(
f”{BINANCE_REST}/api/v3/ticker/24hr”,
timeout=aiohttp.ClientTimeout(total=30),
) as resp:
data = await resp.json()
return {
t["symbol”]: {
"price”: float(t["lastPrice”]),
"change_pct”: float(t["priceChangePercent”]),
"volume_usdt”: float(t["quoteVolume”]),
"high_24h”: float(t["highPrice”]),
"low_24h”: float(t["lowPrice”]),
"volume_base”: float(t["volume”]),
}
for t in data if t["symbol”].endswith("USDT”)
}
except Exception as e:
print(f”[CRYPTO] ERREUR fetch_tickers_24h : {e}”)
return {}

async def fetch_klines(session: aiohttp.ClientSession, symbol: str) -> list[dict]:
try:
async with session.get(
f”{BINANCE_REST}/api/v3/klines”,
params={"symbol”: symbol, "interval”: KLINE_INTERVAL, "limit”: KLINE_LIMIT},
timeout=aiohttp.ClientTimeout(total=15),
) as resp:
raw = await resp.json()
return [
{
"open”: float(k[1]), "high”: float(k[2]),
"low”: float(k[3]), "close”: float(k[4]),
"volume”: float(k[5]), "quote_volume”: float(k[7]),
"trades”: int(k[8]),
}
for k in raw
]
except Exception as e:
print(f”[CRYPTO] ERREUR fetch_klines {symbol} : {e}”)
return []

# ─── Indicateurs ──────────────────────────────────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> float | None:
if len(closes) < period + 1:
return None
deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
gains = [max(d, 0) for d in deltas]
losses = [abs(min(d, 0)) for d in deltas]
avg_gain = sum(gains[:period]) / period
avg_loss = sum(losses[:period]) / period
for i in range(period, len(deltas)):
avg_gain = (avg_gain * (period - 1) + gains[i]) / period
avg_loss = (avg_loss * (period - 1) + losses[i]) / period
if avg_loss == 0:
return 100.0
return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

def calc_ema(closes: list[float], period: int) -> list[float]:
if len(closes) < period:
return []
k = 2 / (period + 1)
ema = [sum(closes[:period]) / period]
for price in closes[period:]:
ema.append(price * k + ema[-1] * (1 - k))
return ema

def calc_macd(closes: list[float]) -> dict | None:
if len(closes) < 35:
return None
ema12 = calc_ema(closes, 12)
ema26 = calc_ema(closes, 26)
if not ema12 or not ema26:
return None
diff = len(ema12) - len(ema26)
ema12 = ema12[diff:]
macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
if len(macd_line) < 9:
return None
signal_line = calc_ema(macd_line, 9)
if not signal_line:
return None
crossover = (
len(macd_line) >= 2 and len(signal_line) >= 2
and macd_line[-2] < signal_line[-2]
and macd_line[-1] > signal_line[-1]
)
return {
"macd”: round(macd_line[-1], 8),
"signal”: round(signal_line[-1], 8),
"histogram”: round(macd_line[-1] - signal_line[-1], 8),
"crossover_bullish”: crossover,
}

def calc_atr(candles: list[dict], period: int = 14) -> float | None:
if len(candles) < period + 1:
return None
trs = []
for i in range(1, len(candles)):
h, l, pc = candles[i]["high”], candles[i]["low”], candles[i-1]["close”]
trs.append(max(h - l, abs(h - pc), abs(l - pc)))
return round(sum(trs[-period:]) / period, 8)

def calc_volume_spike(candles: list[dict]) -> dict | None:
if len(candles) < 21:
return None
recent = [c["quote_volume”] for c in candles[-21:-1]]
avg = sum(recent) / len(recent)
current = candles[-1]["quote_volume”]
if avg == 0:
return None
ratio = current / avg
return {
"current”: round(current, 2),
"avg_20h”: round(avg, 2),
"ratio”: round(ratio, 2),
"is_spike”: ratio >= 2.0,
}

def calc_support_resistance(candles: list[dict]) -> dict:
if len(candles) < 10:
return {}
recent = candles[-50:] if len(candles) >= 50 else candles
highs = [c["high”] for c in recent]
lows = [c["low”] for c in recent]
current = candles[-1]["close”]
resistance = max(highs)
support = min(lows)
return {
"support”: round(support, 8),
"resistance”: round(resistance, 8),
"breakout_up”: current >= resistance * 0.99,
"breakdown”: current <= support * 1.01,
"pct_from_resistance”: round((resistance - current) / current * 100, 2),
"pct_from_support”: round((current - support) / current * 100, 2),
}

def calc_momentum(candles: list[dict]) -> dict:
result = {}
if len(candles) >= 2:
result["momentum_1h”] = round(
(candles[-1]["close”] - candles[-2]["close”]) / candles[-2]["close”] * 100, 3)
if len(candles) >= 7:
result["momentum_6h”] = round(
(candles[-1]["close”] - candles[-7]["close”]) / candles[-7]["close”] * 100, 3)
return result

# ─── Scoring optimise ─────────────────────────────────────────────────────────

def compute_score(indicators: dict, ticker: dict) -> tuple[int, list[str]]:
"””
Scoring optimise pour detecter les signaux AVANT le mouvement.

```
Principes :
- Signaux precoces (MACD crossover + RSI bas + volume sans prix) = fort poids
- Signaux tardifs (variation 24h > 8%, RSI > 65, momentum > 7%) = penalite
- Condition bloquante : variation > 10% ET RSI > 65 = score plafonne a 70
"""
score = 0
signals = []

rsi = indicators.get("rsi")
macd = indicators.get("macd")
volume = indicators.get("volume_spike")
sr = indicators.get("support_resistance", {})
momentum = indicators.get("momentum", {})
change_pct = ticker.get("change_pct", 0)
m1h = momentum.get("momentum_1h", 0)
m6h = momentum.get("momentum_6h", 0)

# ── CONDITION BLOQUANTE ──────────────────────────────────────────────────
# Si le prix a deja beaucoup monte ET RSI en surachat = trop tard
if change_pct > 10 and rsi and rsi > 65:
    return 0, []  # Bloque immediatement, pas de signal

# ── RSI ──────────────────────────────────────────────────────────────────
if rsi is not None:
    if rsi <= 35:
        # RSI survendu = opportunite d'achat claire
        score += 28
        signals.append(f"RSI survendu ({rsi}) - zone achat")
    elif rsi <= 45:
        # RSI bas = debut potentiel de rebond
        score += 18
        signals.append(f"RSI bas ({rsi}) - rebond possible")
    elif 45 < rsi <= 60:
        # RSI neutre avec momentum = signal precoce ideal
        score += 10
        signals.append(f"RSI neutre ({rsi}) - momentum sain")
    elif 60 < rsi <= 65:
        # RSI elevé mais acceptable
        score += 5
        signals.append(f"RSI hausse ({rsi})")
    elif rsi > 65:
        # RSI trop eleve = signal tardif, penalite
        score -= 10
        signals.append(f"RSI surachat ({rsi}) - signal tardif")

# ── MACD ─────────────────────────────────────────────────────────────────
if macd:
    if macd["crossover_bullish"]:
        # Crossover = meilleur signal precoce possible
        # Bonus supplementaire si RSI encore bas (combinaison ideale)
        if rsi and rsi <= 60:
            score += 35
            signals.append("MACD crossover haussier + RSI sain (signal precoce fort)")
        else:
            score += 25
            signals.append("MACD crossover haussier")
    elif macd["histogram"] > 0 and macd["macd"] > 0:
        score += 8
        signals.append("MACD positif")

# ── VOLUME ───────────────────────────────────────────────────────────────
if volume and volume["is_spike"]:
    ratio = volume["ratio"]
    # Volume spike SANS grosse variation de prix = accumulation silencieuse
    if abs(change_pct) < 3:
        # Tres fort signal : beaucoup d'acheteurs, prix pas encore monte
        if ratio >= 3:
            score += 30
            signals.append(f"Accumulation silencieuse x{ratio:.1f} (prix stable)")
        else:
            score += 20
            signals.append(f"Volume inhabituel x{ratio:.1f} sans mouvement prix")
    elif abs(change_pct) < 8:
        # Volume avec mouvement modere = acceptable
        if ratio >= 5:
            score += 20
            signals.append(f"Volume spike x{ratio:.1f} vs moyenne")
        elif ratio >= 3:
            score += 13
            signals.append(f"Volume spike x{ratio:.1f} vs moyenne")
        else:
            score += 8
            signals.append(f"Volume spike x{ratio:.1f} vs moyenne")
    else:
        # Volume avec gros mouvement deja fait = signal tardif, poids reduit
        score += 5
        signals.append(f"Volume spike x{ratio:.1f} (mouvement deja en cours)")

# ── CASSURE RESISTANCE ────────────────────────────────────────────────────
if sr:
    if sr.get("breakout_up"):
        # Cassure propre si variation encore moderee
        if change_pct < 5:
            score += 22
            signals.append(f"Cassure resistance propre (${sr['resistance']:.6f})")
        elif change_pct < 8:
            score += 15
            signals.append(f"Cassure resistance (${sr['resistance']:.6f})")
        else:
            # Cassure apres gros mouvement = deja prise en compte
            score += 5
            signals.append(f"Cassure resistance tardive (${sr['resistance']:.6f})")
    elif sr.get("pct_from_resistance", 100) <= 2:
        score += 10
        signals.append(f"Approche resistance ({sr['pct_from_resistance']:.1f}%)")

# ── MOMENTUM ─────────────────────────────────────────────────────────────
# Momentum modere = sain. Momentum excessif = trop tard.
if m1h > 0 and m6h > 0:
    if m1h <= 3 and m6h <= 6:
        # Momentum propre et progressif = bon signe
        score += 12
        signals.append(f"Momentum progressif 1h +{m1h:.1f}% / 6h +{m6h:.1f}%")
    elif m1h <= 5 and m6h <= 10:
        # Momentum fort mais encore acceptable
        score += 6
        signals.append(f"Momentum 1h +{m1h:.1f}% / 6h +{m6h:.1f}%")
    else:
        # Momentum excessif = penalite
        score -= 8
        signals.append(f"Momentum excessif 1h +{m1h:.1f}% / 6h +{m6h:.1f}% - risque retracement")

# ── VARIATION 24H ─────────────────────────────────────────────────────────
# La variation 24h n'est PLUS un signal positif.
# Elle est neutre jusqu'a 5%, penalisante au-dela.
if change_pct > 8:
    score -= 10
    signals.append(f"Variation 24h elevee +{change_pct:.1f}% - mouvement deja realise")
elif change_pct > 5:
    score -= 5
    signals.append(f"Variation 24h moderee +{change_pct:.1f}%")

return min(max(score, 0), 100), signals
```

# ─── Analyse d’un symbole ─────────────────────────────────────────────────────

async def analyze_symbol(
session: aiohttp.ClientSession,
symbol: str,
ticker: dict,
) -> dict | None:
candles = await fetch_klines(session, symbol)
if len(candles) < 30:
return None

```
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

# Filtre macro
score, signals = apply_macro_filter(score, signals, "crypto", _macro_cache)

if score < Config.SIGNAL_SCORE_MIN or len(signals) < Config.SIGNAL_COUNT_MIN:
    return None

return {
    "symbol": symbol,
    "score": score,
    "signals": signals,
    "indicators": indicators,
    "ticker": ticker,
    "candles_last": candles[-3:],
    "macro": _macro_cache,
    "timestamp": datetime.now(timezone.utc).isoformat(),
}
```

# ─── Boucle principale ────────────────────────────────────────────────────────

async def run_crypto_scanner(signal_callback):
"”"Boucle principale du scanner crypto.”””
global _macro_cache
print(”[CRYPTO] Demarrage du scanner crypto…”)

```
headers = {}
if Config.BINANCE_API_KEY:
    headers["X-MBX-APIKEY"] = Config.BINANCE_API_KEY

while True:
    try:
        scan_start = time.time()
        print(f"[CRYPTO] Debut du scan - {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

        _macro_cache = await get_macro_context()
        regime = _macro_cache.get("regime_crypto", "NEUTRAL")
        btc_change = _macro_cache.get("btc", {}).get("change_24h", 0)
        print(f"[CRYPTO] Macro : BTC {btc_change:+.1f}% - Regime {regime}")

        async with aiohttp.ClientSession(headers=headers) as session:
            symbols = await fetch_all_usdt_symbols(session)
            if not symbols:
                await asyncio.sleep(60)
                continue

            tickers = await fetch_tickers_24h(session)
            eligible = [
                s for s in symbols
                if tickers.get(s, {}).get("volume_usdt", 0) >= MIN_VOLUME_USDT
            ]
            print(f"[CRYPTO] {len(eligible)} paires avec volume >= ${MIN_VOLUME_USDT:,.0f}")

            qualified = []
            for i in range(0, len(eligible), 10):
                batch = eligible[i:i + 10]
                tasks = [
                    analyze_symbol(session, sym, tickers.get(sym, {}))
                    for sym in batch
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, dict):
                        qualified.append(result)
                await asyncio.sleep(0.5)

            print(f"[CRYPTO] {len(qualified)} signaux qualifies detectes.")

            for result in sorted(qualified, key=lambda x: x["score"], reverse=True):
                sym = result["symbol"]
                if time.time() - _last_alert.get(sym, 0) > SCAN_INTERVAL:
                    _last_alert[sym] = time.time()
                    await signal_callback(result)

        elapsed = round(time.time() - scan_start, 1)
        print(f"[CRYPTO] Scan termine en {elapsed}s. Prochain scan dans {SCAN_INTERVAL}s.")

    except Exception as e:
        print(f"[CRYPTO] ERREUR scan complet : {e}")

    await asyncio.sleep(SCAN_INTERVAL)
```
