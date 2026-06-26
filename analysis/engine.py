import json
import aiohttp
from datetime import datetime, timezone

from config import Config


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


async def call_claude(prompt: str) -> str | None:
    """
    Appelle Claude API (Sonnet) et retourne la réponse texte.
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
    Produit une analyse IA complète pour un signal crypto qualifié.
    Enrichit le dict result avec : ai_analysis, entry, target, stop, ratio, conviction.
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

    prompt = f"""Tu es un trader quantitatif professionnel. Analyse ce signal crypto de manière factuelle et concise.

ACTIF : {symbol}
PRIX ACTUEL : ${price:.8f}
VARIATION 24H : {change_24h:+.2f}%
VOLUME 24H : ${volume_usdt:,.0f}
SCORE : {score}/100

SIGNAUX DÉTECTÉS :
{chr(10).join(f'- {s}' for s in signals)}

INDICATEURS :
- RSI (14) : {rsi if rsi else 'N/A'}
- MACD crossover haussier : {macd.get('crossover_bullish', False) if macd else 'N/A'}
- ATR (14) : {atr if atr else 'N/A'}
- Support : ${sr.get('support', 0):.8f}
- Résistance : ${sr.get('resistance', 0):.8f}

Réponds UNIQUEMENT avec ce JSON (sans markdown, sans texte avant ou après) :
{{
  "analyse": "2 phrases max, factuel, contexte marché uniquement",
  "entree": {price:.8f},
  "cible": <prix cible calculé sur ATR x2 ou résistance suivante>,
  "stop": <prix stop calculé sur ATR x1 ou support>,
  "ratio": <ratio risque/récompense arrondi à 1 décimale>,
  "conviction": "FORTE|MOYENNE|FAIBLE",
  "action": "ENTRER|SURVEILLER|EVITER"
}}

Règles strictes :
- Entrée = prix actuel
- Stop = prix actuel - ATR (si ATR disponible) ou support le plus proche
- Cible = prix actuel + (ATR x 2) ou résistance suivante
- Conviction FORTE uniquement si score >= 85 et >= 4 signaux
- Conviction FAIBLE si score < 80 ou signaux contradictoires
- Action EVITER si RSI > 75 (surachat) ou momentum négatif"""

    response = await call_claude(prompt)

    # Valeurs par défaut si Claude échoue
    default = {
        "analyse": f"Signal technique détecté sur {symbol} avec {len(signals)} confirmations.",
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
        # Nettoie la réponse au cas où Claude ajoute du markdown
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = json.loads(clean.strip())
        result["ai"] = parsed
    except Exception as e:
        print(f"[ENGINE] Erreur parsing réponse Claude ({symbol}) : {e}")
        result["ai"] = default

    return result


def format_crypto_alert(result: dict) -> str:
    """
    Formate le message Telegram pour une alerte crypto.
    Respecte exactement le format défini dans le cahier des charges.
    """
    symbol = result["symbol"]
    score = result["score"]
    ticker = result["ticker"]
    signals = result["signals"]
    ai = result.get("ai", {})

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
    analyse = ai.get("analyse", "Signal technique détecté.")

    if entree > 0 and cible > 0:
        pct_target = round((cible - entree) / entree * 100, 1)
    else:
        pct_target = 0

    if entree > 0 and stop > 0:
        pct_stop = round((stop - entree) / entree * 100, 1)
    else:
        pct_stop = 0

    # Emoji conviction
    conviction_emoji = {"FORTE": "🟢", "MOYENNE": "🟡", "FAIBLE": "🔴"}.get(conviction, "🟡")
    action_emoji = {"ENTRER": "✅", "SURVEILLER": "👁", "EVITER": "❌"}.get(action, "👁")

    signals_text = "\n".join(f"  • {s}" for s in signals)

    msg = (
        f"<b>🔔 CRYPTO SIGNAL — Score {score}/100</b>\n"
        f"<b>{base} (#{symbol})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Prix : <b>${price:.6f}</b>\n"
        f"Variation 24h : <b>{change_24h:+.2f}%</b>\n"
        f"Volume 24h : <b>${volume_usdt:,.0f}</b>\n"
        f"\n<b>Signaux détectés :</b>\n{signals_text}\n"
        f"\nSources : Binance Spot\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>ANALYSE :</b>\n{analyse}\n"
        f"\nEntrée : <b>${entree:.6f}</b>\n"
        f"Cible : <b>${cible:.6f}</b> ({pct_target:+.1f}%)\n"
        f"Stop : <b>${stop:.6f}</b> ({pct_stop:+.1f}%)\n"
        f"Ratio : <b>1:{ratio}</b>\n"
        f"Conviction : {conviction_emoji} <b>{conviction}</b>\n"
        f"Action : {action_emoji} <b>{action}</b>"
    )

    return msg
