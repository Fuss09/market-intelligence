import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta

from config import Config


# ─── Constantes ───────────────────────────────────────────────────────────────

NEWSAPI_BASE = "https://newsapi.org/v2"
LOOKBACK_HOURS = 24

# Mots-cles exacts qui garantissent des resultats NewsAPI
SECTOR_KEYWORDS = {
    "IA & Semi-conducteurs": [
        "NVIDIA semiconductor",
        "AMD chip AI",
        "artificial intelligence chip",
    ],
    "Biotech & Pharma": [
        "FDA drug approval",
        "clinical trial phase 3 results",
        "biotech acquisition",
    ],
    "Cybersecurite": [
        "cybersecurity contract",
        "cyber attack defense",
    ],
    "Defense & Spatial": [
        "defense contract Pentagon",
        "aerospace space contract",
    ],
    "Quantique": [
        "quantum computing breakthrough",
    ],
    "Energie IA": [
        "nuclear energy data center",
        "SMR reactor contract",
    ],
    "Crypto": [
        "bitcoin institutional",
        "ethereum ETF",
        "crypto regulation",
    ],
}

# Sources de confiance — format NewsAPI (nom de domaine)
TRUSTED_DOMAINS = (
    "reuters.com,bloomberg.com,wsj.com,ft.com,"
    "coindesk.com,theblock.co,biopharmadive.com,"
    "statnews.com,globenewswire.com,businesswire.com,"
    "prnewswire.com,cnbc.com,marketwatch.com,techcrunch.com"
)


# ─── Fetch news ───────────────────────────────────────────────────────────────

async def fetch_news_for_query(
    session: aiohttp.ClientSession,
    query: str,
    sector: str,
) -> list[dict]:
    """
    Recupere les articles via NewsAPI — sans filtre domaine strict
    pour maximiser les resultats, puis filtre par pertinence.
    """
    if not Config.NEWS_API_KEY:
        return []

    results = []
    from_date = (
        datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%S")

    try:
        # Essai 1 : avec domaines de confiance
        async with session.get(
            f"{NEWSAPI_BASE}/everything",
            params={
                "q": query,
                "from": from_date,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 5,
                "apiKey": Config.NEWS_API_KEY,
                "domains": TRUSTED_DOMAINS,
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            articles = data.get("articles", [])

        # Essai 2 : sans filtre domaine si 0 resultats
        if not articles:
            async with session.get(
                f"{NEWSAPI_BASE}/everything",
                params={
                    "q": query,
                    "from": from_date,
                    "language": "en",
                    "sortBy": "relevancy",
                    "pageSize": 5,
                    "apiKey": Config.NEWS_API_KEY,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp2:
                data2 = await resp2.json()
                articles = data2.get("articles", [])

        for article in articles:
            title = article.get("title", "")
            source = article.get("source", {}).get("name", "")
            url = article.get("url", "")
            published = article.get("publishedAt", "")
            description = article.get("description", "")

            if not title or title == "[Removed]":
                continue

            importance = _estimate_importance(title)

            results.append({
                "source": f"NewsAPI ({source})",
                "type": "news",
                "sector": sector,
                "title": title[:120],
                "description": description[:200] if description else "",
                "published": published,
                "url": url,
                "importance": importance,
            })

    except Exception as e:
        print(f"[NEWS] ERREUR fetch '{query}' : {e}")

    return results


def _estimate_importance(title: str) -> str:
    """Estime l'importance d'une news par mots-cles dans le titre."""
    title_lower = title.lower()

    high_keywords = [
        "fda approv", "pdufa", "merger", "acquisition",
        "billion contract", "pentagon", "breakthrough",
        "phase 3", "phase iii", "sec investigation",
    ]
    medium_keywords = [
        "partnership", "contract", "deal", "agreement",
        "phase 2", "phase ii", "earnings", "guidance", "quarterly",
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
    Retourne un dict {secteur: [articles HIGH ou MEDIUM]}.
    """
    if not Config.NEWS_API_KEY:
        print("[NEWS] NEWS_API_KEY non configuree — scan news ignore.")
        return {}

    print("[NEWS] Scan des news professionnelles...")
    results_by_sector = {}

    async with aiohttp.ClientSession() as session:
        for sector, keywords in SECTOR_KEYWORDS.items():
            sector_news = []

            for keyword in keywords[:2]:  # Max 2 requetes par secteur
                articles = await fetch_news_for_query(session, keyword, sector)
                sector_news.extend(articles)
                await asyncio.sleep(0.5)

            # Deduplique par URL
            seen_urls = set()
            unique_news = []
            for article in sector_news:
                if article["url"] not in seen_urls:
                    seen_urls.add(article["url"])
                    unique_news.append(article)

            # Filtre et tri par importance
            filtered = [n for n in unique_news if n["importance"] in ["HIGH", "MEDIUM"]]
            filtered.sort(
                key=lambda x: (0 if x["importance"] == "HIGH" else 1),
            )

            if filtered:
                results_by_sector[sector] = filtered[:4]
                print(f"[NEWS] {sector} : {len(filtered)} articles")

    total = sum(len(v) for v in results_by_sector.values())
    print(f"[NEWS] Total : {total} articles sur {len(results_by_sector)} secteurs.")

    return results_by_sector


def get_news_for_symbol(news_by_sector: dict, symbol: str, sector: str) -> list[dict]:
    """Filtre les news pertinentes pour un symbole specifique."""
    sector_news = news_by_sector.get(sector, [])
    base = (
        symbol.replace("USDT", "")
        .replace(".PA", "").replace(".DE", "")
        .replace(".L", "").replace(".AS", "")
        .upper()
    )

    return [a for a in sector_news if base in a.get("title", "").upper()]
