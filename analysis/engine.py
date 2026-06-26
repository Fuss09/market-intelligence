import json
import aiohttp
from datetime import datetime, timezone

from config import Config


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


async def call_claude(prompt: str) -> str | None:
    """
    Appelle Claude API (Sonnet) et retourne la reponse texte.
    Retourne None en cas d'erreur.
    """
    headers = {
        "x-api-key": Config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": Config.ANTHROPIC_MODEL,
        "max_tokens": Config.ANTHROPIC_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()

                if resp.status != 200:
                    print(f"[ENGINE] Erreur Claude API : {data.get('error', {}).get('message')}")
                    return None

                return data["content"][0]["text"]

    except Exception as e:
        print(f"[ENGINE] Exception Claude API : {e}")
        return None


async def analyze_crypto_signal(result: dict) -> dict:
    """
    Produit une analyse IA complete pour un signal crypto qualifie.
    """
    symbol = result["symbol"]
    ticker = result["ticker"]
    signals = result["signals"]
    indicators = result["indicators"]
    score = result["score"]

    price = ticker.get("price", 0)
    change_24h = ticker.get("change_pct", 0)
    volume_usdt = ticker.get("volume_usdt", 0)
    atr = indicators.get("atr")
    rsi = indicators.get("rsi")
    macd = indicators.get("macd")
    sr = indicators.get("support_resistance", {})

    # Sources externes (news, gouvernementales)
    external_sources = result.get("external_sources", [])
    external_context = ""
    if external_sources:
        external_context = "\nCATALYSEURS EXTERNES DETECTES :\n"
        for src in external_sources[:3]:
            external_context += f"- [{src.get('source')}] {src.get('description', '')}\n"

    prompt = f"""Tu es un trader quantitatif professionnel. Analyse ce signal crypto de maniere factuelle.

ACTIF : {symbol}
PRIX : ${price:.8f}
VARIATION 24H : {change_24h:+.2f}%
VOLUME 24H : ${volume_usdt:,.0f}
SCORE : {score}/100

SIGNAUX DETECTES :
{chr(10).join(f'- {s}' for s in signals)}

INDICATEURS :
- RSI (14) : {rsi if rsi else 'N/A'}
- MACD crossover haussier : {macd.get('crossover_bullish', False) if macd else 'N/A'}
- ATR (14) : {atr if atr else 'N/A'}
- Support : ${sr.get('support', 0):.8f}
- Resistance : ${sr.get('resistance', 0):.8f}
{external_context}

Reponds UNIQUEMENT avec ce JSON (sans markdown) :
{{
  "analyse": "2 phrases max, factuel, contexte marche uniquement",
  "entree": {price:.8f},
  "cible": <prix cible calcule sur ATR x2 ou resistance suivante>,
  "stop": <prix stop calcule sur ATR x1 ou support>,
  "ratio": <ratio risque/recompense arrondi a 1 decimale>,
  "conviction": "FORTE|MOYENNE|FAIBLE",
  "action": "ENTRER|SURVEILLER|EVITER"
}}

Regles :
- Stop = prix actuel - ATR ou support le plus proche
- Cible = prix actuel + (ATR x 2) ou resistance suivante
- Conviction FORTE uniquement si score >= 85 et >= 4 signaux
- Action EVITER si RSI > 75 ou momentum negatif"""

    response = await call_claude(prompt)

    default = {
        "analyse": f"Signal technique detecte sur {symbol} avec {len(signals)} confirmations.",
        "entree": round(price, 8),
        "cible": round(price * 1.05, 8),
        "stop": round(price * 0.97, 8),
        "ratio": 1.7,
        "conviction": "MOYENNE",
        "action": "SURVEILLER",
    }

    if not response:
        result["ai"] = default
        return result

    try:
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        result["ai"] = json.loads(clean.strip())
    except Exception as e:
        print(f"[ENGINE] Erreur parsing Claude ({symbol}) : {e}")
        result["ai"] = default

    return result


def format_crypto_alert(result: dict) -> str:
    """
    Formate le message Telegram pour une alerte crypto.
    """
    symbol = result["symbol"]
    score = result["score"]
    ticker = result["ticker"]
    signals = result["signals"]
    ai = result.get("ai", {})
    external = result.get("external_sources", [])

    price = ticker.get("price", 0)
    change_24h = ticker.get("change_pct", 0)
    volume_usdt = ticker.get("volume_usdt", 0)
    base = symbol.replace("USDT", "")

    entree = ai.get("entree", price)
    cible = ai.get("cible", price * 1.05)
    stop = ai.get("stop", price * 0.97)
    ratio = ai.get("ratio", 1.7)
    conviction = ai.get("conviction", "MOYENNE")
    action = ai.get("action", "SURVEILLER")
    analyse = ai.get("analyse", "Signal technique detecte.")

    pct_target = round((cible - entree) / entree * 100, 1) if entree > 0 else 0
    pct_stop = round((stop - entree) / entree * 100, 1) if entree > 0 else 0

    conviction_emoji = {"FORTE": "🟢", "MOYENNE": "🟡", "FAIBLE": "🔴"}.get(conviction, "🟡")
    action_emoji = {"ENTRER": "✅", "SURVEILLER": "👁", "EVITER": "❌"}.get(action, "👁")

    signals_text = "\n".join(f"  . {s}" for s in signals)

    # Sources externes
    sources_text = "Binance Spot"
    if external:
        extra = [e.get("source", "") for e in external[:2]]
        sources_text += " + " + " + ".join(extra)

    # Catalyseurs externes si presents
    catalyst_block = ""
    if external:
        catalyst_block = "\nCatalyseurs :\n"
        for e in external[:2]:
            catalyst_block += f"  . [{e.get('source')}] {e.get('description', '')[:80]}\n"

    msg = (
        f"<b>CRYPTO SIGNAL - Score {score}/100</b>\n"
        f"<b>{base} ({symbol})</b>\n"
        f"- - - - - - - - - - - - -\n"
        f"Prix : <b>${price:.6f}</b>\n"
        f"Variation 24h : <b>{change_24h:+.2f}%</b>\n"
        f"Volume 24h : <b>${volume_usdt:,.0f}</b>\n"
        f"\n<b>Signaux :</b>\n{signals_text}\n"
        f"{catalyst_block}"
        f"\nSources : {sources_text}\n"
        f"- - - - - - - - - - - - -\n"
        f"<b>ANALYSE :</b>\n{analyse}\n"
        f"\nEntree : <b>${entree:.6f}</b>\n"
        f"Cible : <b>${cible:.6f}</b> ({pct_target:+.1f}%)\n"
        f"Stop : <b>${stop:.6f}</b> ({pct_stop:+.1f}%)\n"
        f"Ratio : <b>1:{ratio}</b>\n"
        f"Conviction : {conviction_emoji} <b>{conviction}</b>\n"
        f"Action : {action_emoji} <b>{action}</b>"
    )

    return msg
