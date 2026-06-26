import aiohttp
from datetime import datetime, timezone

from config import Config
from analysis.engine import call_claude


# ─── Analyse IA par secteur ───────────────────────────────────────────────────

async def analyze_stock_signal(result: dict) -> dict:
    """
    Enrichit un signal bourse avec une analyse IA Claude.
    """
    symbol = result["symbol"]
    sector = result["sector"]
    score = result["score"]
    signals = result["signals"]
    quote = result["quote"]
    technical = result["technical"]

    price = quote.get("price", 0)
    change_pct = quote.get("change_pct", 0)
    volume = quote.get("volume", 0)
    market_cap = quote.get("market_cap", 0)
    rsi = technical.get("rsi")
    macd = technical.get("macd", {})

    prompt = f"""Tu es un analyste financier professionnel spécialisé en {sector}.
Analyse ce signal boursier de manière factuelle et concise.

ACTIF : {symbol}
SECTEUR : {sector}
PRIX : ${price:.2f}
VARIATION JOUR : {change_pct:+.2f}%
CAPITALISATION : ${market_cap:,.0f}
VOLUME : {volume:,.0f}
SCORE : {score}/100

SIGNAUX TECHNIQUES :
{chr(10).join(f'- {s}' for s in signals)}

INDICATEURS :
- RSI (14j) : {rsi if rsi else 'N/A'}
- MACD crossover : {macd.get('crossover_bullish', False) if macd else 'N/A'}

Réponds UNIQUEMENT avec ce JSON (sans markdown) :
{{
  "catalyseur": "catalyseur principal factuel en 1 phrase (technique si pas de news)",
  "timing": "Immédiat|Cette semaine|Ce mois",
  "potentiel_hausse": <pourcentage entier>,
  "risque_baisse": <pourcentage entier>,
  "niveau_risque": "FAIBLE|MOYEN|ELEVE|TRES ELEVE",
  "entree": <prix entrée>,
  "cible": <prix cible>,
  "stop": <prix stop>,
  "verdict": "OPPORTUNITE|SURVEILLER|EVITER"
}}

Règles :
- Catalyseur uniquement factuel (signal technique = acceptable)
- Potentiel hausse basé sur résistances techniques
- Stop toujours en dessous du support le plus proche
- Niveau risque ELEVE si biotech sans données publiées
- Verdict OPPORTUNITE uniquement si score >= 80"""

    response = await call_claude(prompt)

    default = {
        "catalyseur": f"Signal technique multi-confirmations sur {symbol}.",
        "timing": "Cette semaine",
        "potentiel_hausse": 8,
        "risque_baisse": 5,
        "niveau_risque": "MOYEN",
        "entree": price,
        "cible": round(price * 1.08, 2),
        "stop": round(price * 0.95, 2),
        "verdict": "SURVEILLER",
    }

    if not response:
        result["ai"] = default
        return result

    try:
        import json
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        result["ai"] = json.loads(clean.strip())
    except Exception:
        result["ai"] = default

    return result


# ─── Contexte macro ───────────────────────────────────────────────────────────

async def generate_macro_context(results_by_sector: dict) -> str:
    """
    Génère le contexte macro du jour basé sur les signaux détectés.
    """
    total_signals = sum(len(v) for v in results_by_sector.values())
    active_sectors = [s for s, v in results_by_sector.items() if v]

    prompt = f"""Tu es un stratégiste de marché senior. Rédige le contexte macro du jour en 3 phrases maximum.

DONNÉES DU SCAN :
- Signaux qualifiés détectés : {total_signals}
- Secteurs actifs : {', '.join(active_sectors) if active_sectors else 'Aucun'}
- Date : {datetime.now(timezone.utc).strftime('%d/%m/%Y')}

Règles strictes :
- Uniquement des faits observables (signaux techniques, activité de marché)
- Zéro spéculation, zéro opinion
- Si 0 signaux : indiquer marché calme et consolider
- 3 phrases maximum
- Pas de titre, pas de markdown"""

    response = await call_claude(prompt)

    if not response:
        if total_signals == 0:
            return "Marché en phase de consolidation. Aucun signal qualifié détecté ce matin sur l'ensemble des secteurs surveillés. Conditions favorables à l'observation plutôt qu'à l'action."
        return f"{total_signals} signal(s) qualifié(s) détecté(s) ce matin sur {len(active_sectors)} secteur(s). Activité concentrée sur : {', '.join(active_sectors[:3])}."

    return response.strip()


# ─── Formatage du morning brief ───────────────────────────────────────────────

def format_sector_block(sector: str, signals: list[dict]) -> str:
    """Formate le bloc d'un secteur pour le morning brief."""

    sector_upper = sector.upper()
    header = f"\n─── {sector_upper} ───"

    if not signals:
        return f"{header}\nAucun signal qualifié."

    blocks = []
    for result in sorted(signals, key=lambda x: x["score"], reverse=True):
        symbol = result["symbol"]
        score = result["score"]
        quote = result["quote"]
        ai = result.get("ai", {})

        price = quote.get("price", 0)
        change_pct = quote.get("change_pct", 0)
        exchange = quote.get("exchange", "")

        catalyseur = ai.get("catalyseur", "Signal technique.")
        timing = ai.get("timing", "Cette semaine")
        potentiel = ai.get("potentiel_hausse", 0)
        risque_down = ai.get("risque_baisse", 0)
        niveau_risque = ai.get("niveau_risque", "MOYEN")
        entree = ai.get("entree", price)
        cible = ai.get("cible", price)
        stop = ai.get("stop", price)
        verdict = ai.get("verdict", "SURVEILLER")

        verdict_emoji = {"OPPORTUNITE": "✅", "SURVEILLER": "👁", "EVITER": "❌"}.get(verdict, "👁")
        risque_emoji = {"FAIBLE": "🟢", "MOYEN": "🟡", "ELEVE": "🔴", "TRES ELEVE": "⛔"}.get(niveau_risque, "🟡")

        block = (
            f"\n<b>{symbol}</b> — Score {score}/100"
            f"\nPrix : ${price:.2f} ({change_pct:+.2f}%) | {exchange}"
            f"\nCatalyseur : {catalyseur}"
            f"\nTiming : {timing}"
            f"\nPotentiel : +{potentiel}% | Risque : -{risque_down}%"
            f"\nNiveau de risque : {risque_emoji} {niveau_risque}"
            f"\nEntrée : ${entree:.2f} | Cible : ${cible:.2f} | Stop : ${stop:.2f}"
            f"\nVerdict : {verdict_emoji} <b>{verdict}</b>"
        )
        blocks.append(block)

    return header + "".join(blocks)


async def generate_morning_brief(results_by_sector: dict) -> str:
    """
    Génère le morning brief complet pour FussBourse.
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d/%m/%Y")

    # Enrichissement IA de chaque signal
    for sector, signals in results_by_sector.items():
        for i, result in enumerate(signals):
            results_by_sector[sector][i] = await analyze_stock_signal(result)

    # Contexte macro
    macro = await generate_macro_context(results_by_sector)

    # Construction du brief
    header = (
        f"<b>📊 MORNING BRIEF — {date_str}</b>\n"
        f"{'═' * 26}\n"
        f"<b>MACRO DU JOUR</b>\n"
        f"{macro}\n"
    )

    sector_blocks = ""
    for sector in SECTORS_ORDER:
        signals = results_by_sector.get(sector, [])
        sector_blocks += format_sector_block(sector, signals)

    total = sum(len(v) for v in results_by_sector.values())
    footer = (
        f"\n{'─' * 26}\n"
        f"Scan : {total} signal(s) qualifié(s) | "
        f"Sources : Yahoo Finance + Twelve Data + Claude AI\n"
        f"{now.strftime('%H:%M UTC')}"
    )

    return header + sector_blocks + footer


# Ordre d'affichage des secteurs dans le brief
SECTORS_ORDER = [
    "IA & Semi-conducteurs",
    "Biotech & Pharma",
    "Quantique",
    "Défense & Spatial",
    "Energie IA",
    "Cybersécurité",
    "Robotique & Automation",
    "Infrastructure IA",
    "Stockage & Mémoire",
]
