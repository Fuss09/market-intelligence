import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta

from config import Config


# ─── Constantes ───────────────────────────────────────────────────────────────

NEWSAPI_BASE = "https://newsapi.org/v2"
LOOKBACK_HOURS = 24

# Sources professionnelles uniquement — zero blog, zero opinion
TRUSTED_SOURCES = [
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "coindesk.com",
    "theblock.co",
    "biopharmadive.com",
    "statnews.com",
    "globenewswire.com",
    "businesswire.com",
    "prnewswire.com",
    "sec.gov",
    "fda.gov",
]

# Mots-cles par secteur pour cibler les news pertinentes
SECTOR_KEYWORDS = {
    "IA & Semi-conducteurs": [
        "nvidia chip", "AMD GPU", "semiconductor", "AI chip",
        "data center GPU", "TSMC", "Intel foundry",
    ],
    "Biotech & Pharma": [
        "FDA approval", "clinical trial phase 3", "drug approval",
        "biotech acquisition", "EMA approval", "PDUFA",
    ],
    "Cybersecurite": [
        "cybersecurity contract", "zero-day", "ransomware defense",
        "security breach", "cyber acquisition",
    ],
    "Defense & Spatial": [
        "defense contract", "Pentagon award", "space contract",
        "missile defense", "satellite launch", "NATO contract",
    ],
    "Quantique": [
        "quantum computing", "quantum breakthrough",
        "qubit", "quantum error correction",
    ],
    "Energie IA": [
        "nuclear energy deal", "SMR reactor", "data center power",
        "nuclear plant", "renewable energy AI",
    ],
    "Crypto": [
        "bitcoin ETF", "ethereum", "crypto regulation",
        "stablecoin", "crypto institutional",
    ],
}


# ─── Fetch news ───────────────────────────────────────────────────────────────

async def fetch_news_for_query(
    session: aiohttp.ClientSession,
    query: str,
    sector: str,
) -> list[dict]:
    """
    Recupere les articles pour une requete donnee via NewsAPI.
    Filtre sur les sources professionnelles uniquement.
    """
    if not Config.NEWS_API_KEY:
        return []

    results = []
    from_date = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")

    try:
        async with session.get(
            f"{NEWSAPI_BASE}/everything",
            params={
                "q": query,
                "from": from_date,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 5,
                "apiKey": Config.NEWS_API_KEY,
                "domains": ",".join(TRUSTED_SOURCES),
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return results
            data = await resp.json()

        for article in data.get("articles", []):
            title = article.get("title", "")
            source = article.get("source", {}).get("name", "")
            url = article.get("url", "")
            published = article.get("publishedAt", "")
            description = article.get("description", "")

            if not title or title == "[Removed]":
                continue

            results.append({
                "source": f"NewsAPI ({source})",
                "type": "news",
                "sector": sector,
                "title": title[:120],
                "description": description[:200] if description else "",
                "published": published,
                "url": url,
                "importance": _estimate_importance(title, sector),
            })

    except Exception as e:
        print(f"[NEWS] ERREUR fetch '{query}' : {e}")

    return results


def _estimate_importance(title: str, sector: str) -> str:
    """Estime l'importance d'une news par mots-cles dans le titre."""
    title_lower = title.lower()

    high_keywords = [
        "fda approval", "fda approved", "pdufa", "merger", "acquisition",
        "billion contract", "pentagon", "breakthrough", "phase 3",
        "sec investigation", "bankruptcy", "acquisition",
    ]
    medium_keywords = [
        "partnership", "contract", "deal", "agreement", "phase 2",
        "quarterly results", "earnings", "guidance",
    ]

    for kw in high_keywords:
        if kw in title_lower:
            return "HIGH"
    for kw in medium_keywords:
        if kw in title_lower:
            return "MEDIUM"

    return "LOW"


# ─── Scanner principal ────────────────────────────────────────────────────────

async def run_news_scan() -> dict[str, list[dict]]:
    """
    Scan complet des news par secteur.
    Retourne un dict {secteur: [articles]}.
    """
    if not Config.NEWS_API_KEY:
        print("[NEWS] NEWS_API_KEY non configuree — scan news ignore.")
        return {}

    print("[NEWS] Scan des news professionnelles...")
    results_by_sector = {}

    async with aiohttp.ClientSession() as session:
        for sector, keywords in SECTOR_KEYWORDS.items():
            sector_news = []

            for keyword in keywords[:3]:  # Max 3 requetes par secteur
                articles = await fetch_news_for_query(session, keyword, sector)
                sector_news.extend(articles)
                await asyncio.sleep(0.3)  # Rate limit NewsAPI

            # Deduplique par URL
            seen_urls = set()
            unique_news = []
            for article in sector_news:
                if article["url"] not in seen_urls:
                    seen_urls.add(article["url"])
                    unique_news.append(article)

            # Garde uniquement HIGH et MEDIUM
            filtered = [n for n in unique_news if n["importance"] in ["HIGH", "MEDIUM"]]
            filtered.sort(key=lambda x: (
                0 if x["importance"] == "HIGH" else 1,
                x.get("published", ""),
            ), reverse=False)

            if filtered:
                results_by_sector[sector] = filtered[:5]

    total = sum(len(v) for v in results_by_sector.values())
    print(f"[NEWS] {total} articles pertinents detectes sur {len(results_by_sector)} secteurs.")

    return results_by_sector


def get_news_for_symbol(news_by_sector: dict, symbol: str, sector: str) -> list[dict]:
    """
    Filtre les news pertinentes pour un symbole specifique.
    Recherche le symbole dans les titres des articles du meme secteur.
    """
    sector_news = news_by_sector.get(sector, [])
    symbol_base = symbol.replace("USDT", "").replace(".PA", "").replace(".DE", "").replace(".L", "")

    matched = []
    for article in sector_news:
        title = article.get("title", "").upper()
        if symbol_base.upper() in title:
            matched.append(article)

    return matched
