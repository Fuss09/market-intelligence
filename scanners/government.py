import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta


# ─── Constantes ───────────────────────────────────────────────────────────────

SEC_BASE = "https://efts.sec.gov/LATEST/search-index"
USASPENDING_BASE = "https://api.usaspending.gov/api/v2"
USPTO_BASE = "https://api.patentsview.org/patents/query"

LOOKBACK_DAYS = 7


# ─── SEC EDGAR Form 4 ─────────────────────────────────────────────────────────

async def fetch_sec_form4(session: aiohttp.ClientSession) -> list[dict]:
    """
    Recupere les Form 4 recents via SEC EDGAR full-text search.
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    try:
        async with session.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q": "form 4",
                "dateRange": "custom",
                "startdt": cutoff,
                "forms": "4",
            },
            headers={"User-Agent": "FussMarketBot contact@example.com"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                print(f"[GOV] SEC Form4 HTTP {resp.status}")
                return results
            data = await resp.json()

        hits = data.get("hits", {}).get("hits", [])
        for hit in hits[:15]:
            source = hit.get("_source", {})
            entity = source.get("entity_name", "")
            file_date = source.get("file_date", "")

            if entity:
                results.append({
                    "source": "SEC EDGAR Form 4",
                    "type": "insider_transaction",
                    "entity": entity,
                    "file_date": file_date,
                    "importance": "MEDIUM",
                    "description": f"Form 4 - {entity} - {file_date}",
                })

    except Exception as e:
        print(f"[GOV] ERREUR SEC Form4 : {e}")

    return results


async def fetch_sec_8k(session: aiohttp.ClientSession, ticker: str) -> list[dict]:
    """Recupere les 8-K recents pour un ticker."""
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    try:
        async with session.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q": ticker,
                "forms": "8-K",
                "dateRange": "custom",
                "startdt": cutoff,
            },
            headers={"User-Agent": "FussMarketBot contact@example.com"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return results
            data = await resp.json()

        hits = data.get("hits", {}).get("hits", [])
        for hit in hits[:5]:
            src = hit.get("_source", {})
            entity = src.get("entity_name", ticker)
            file_date = src.get("file_date", "")

            results.append({
                "source": "SEC EDGAR 8-K",
                "type": "major_announcement",
                "entity": entity,
                "ticker": ticker,
                "file_date": file_date,
                "importance": "HIGH",
                "description": f"8-K majeur - {entity} - {file_date}",
            })

    except Exception as e:
        print(f"[GOV] ERREUR SEC 8-K {ticker} : {e}")

    return results


# ─── USASpending.gov ──────────────────────────────────────────────────────────

async def fetch_usa_contracts(session: aiohttp.ClientSession) -> list[dict]:
    """Recupere les contrats gouvernementaux US >= 10M$."""
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    naics_codes = ["541715", "334413", "541330", "541511", "336411"]

    try:
        payload = {
            "filters": {
                "time_period": [{"start_date": cutoff, "end_date": today}],
                "naics_codes": naics_codes,
                "award_type_codes": ["A", "B", "C", "D"],
            },
            "fields": [
                "Recipient Name", "Award Amount",
                "Awarding Agency Name", "Description", "Action Date",
            ],
            "page": 1,
            "limit": 15,
            "sort": "Award Amount",
            "order": "desc",
        }

        async with session.post(
            f"{USASPENDING_BASE}/search/spending_by_award/",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                print(f"[GOV] USASpending HTTP {resp.status}")
                return results
            data = await resp.json()

        for award in data.get("results", []):
            amount = award.get("Award Amount", 0) or 0
            if amount >= 10_000_000:
                recipient = award.get("Recipient Name", "Unknown")
                agency = award.get("Awarding Agency Name", "")
                date = award.get("Action Date", "")
                importance = "HIGH" if amount >= 100_000_000 else "MEDIUM"

                results.append({
                    "source": "USASpending.gov",
                    "type": "government_contract",
                    "recipient": recipient,
                    "amount_usd": amount,
                    "agency": agency,
                    "date": date,
                    "importance": importance,
                    "description": f"Contrat ${amount:,.0f} - {recipient} - {agency}",
                })

    except Exception as e:
        print(f"[GOV] ERREUR USASpending : {e}")

    return results


# ─── USPTO — API correcte ─────────────────────────────────────────────────────

async def fetch_uspto_patents(
    session: aiohttp.ClientSession,
    keywords: list[str],
) -> list[dict]:
    """
    Recupere les brevets US recents via PatentsView API (URL correcte).
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # Requete simple par mot-cle dans le titre
    query_term = keywords[0] if keywords else "artificial intelligence"

    try:
        async with session.get(
            "https://search.patentsview.org/api/v1/patent/",
            params={
                "q": f'{{"_and":[{{"_gte":{{"patent_date":"{cutoff}"}}}},{{"_text_any":{{"patent_title":"{query_term}"}}}}]}}',
                "f": '["patent_id","patent_title","patent_date","assignees"]',
                "s": '[{"patent_date":"desc"}]',
                "o": '{"per_page":5}',
            },
            headers={
                "User-Agent": "FussMarketBot/1.0",
                "Accept": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                print(f"[GOV] USPTO HTTP {resp.status}")
                return results
            data = await resp.json()

        for patent in data.get("patents", []) or []:
            title = patent.get("patent_title", "")
            date = patent.get("patent_date", "")
            assignees = patent.get("assignees", []) or []
            org = assignees[0].get("assignee_organization", "Unknown") if assignees else "Unknown"
            patent_id = patent.get("patent_id", "")

            if title:
                results.append({
                    "source": "USPTO PatentsView",
                    "type": "patent_us",
                    "patent_id": patent_id,
                    "title": title[:100],
                    "organization": org,
                    "date": date,
                    "importance": "MEDIUM",
                    "description": f"Brevet US - {org} - {title[:60]}",
                })

    except Exception as e:
        print(f"[GOV] ERREUR USPTO : {e}")

    return results


# ─── EPO simplifie ────────────────────────────────────────────────────────────

async def fetch_epo_patents(
    session: aiohttp.ClientSession,
    keywords: list[str],
) -> list[dict]:
    """
    EPO via Espacenet search (alternative publique sans auth).
    """
    results = []
    query = "+".join(keywords[:2])

    try:
        async with session.get(
            "https://worldwide.espacenet.com/3.2/rest-services/published-data/search/biblio",
            params={"q": f"ti={query}"},
            headers={"Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status not in [200, 206]:
                return results
            data = await resp.json()

        entries = (
            data.get("ops:world-patent-data", {})
            .get("ops:biblio-search", {})
            .get("ops:search-result", {})
            .get("exchange-documents", [])
        )

        if isinstance(entries, dict):
            entries = [entries]

        for entry in entries[:3]:
            doc = entry.get("exchange-document", {})
            if isinstance(doc, list):
                doc = doc[0]

            biblio = doc.get("bibliographic-data", {})
            titles = biblio.get("invention-title", [])
            if isinstance(titles, dict):
                titles = [titles]

            title = next(
                (t.get("$", "") for t in titles if t.get("@lang") == "en"),
                titles[0].get("$", "") if titles else "",
            )

            if title:
                results.append({
                    "source": "EPO Espacenet",
                    "type": "patent_eu",
                    "title": title[:100],
                    "importance": "MEDIUM",
                    "description": f"Brevet EP - {title[:60]}",
                })

    except Exception as e:
        print(f"[GOV] ERREUR EPO : {e}")

    return results


# ─── Scanner principal ────────────────────────────────────────────────────────

PATENT_KEYWORDS = [
    "artificial intelligence",
    "semiconductor",
    "quantum computing",
    "cybersecurity",
    "nuclear reactor",
]


async def run_government_scan() -> dict:
    """Scan complet : SEC Form4, USASpending, USPTO, EPO."""
    print("[GOV] Scan sources reglementaires et gouvernementales...")

    async with aiohttp.ClientSession() as session:
        form4, contracts, patents_us, patents_eu = await asyncio.gather(
            fetch_sec_form4(session),
            fetch_usa_contracts(session),
            fetch_uspto_patents(session, PATENT_KEYWORDS),
            fetch_epo_patents(session, PATENT_KEYWORDS[:2]),
        )

    results = {
        "sec_form4": form4,
        "contracts": contracts,
        "patents_us": patents_us,
        "patents_eu": patents_eu,
    }

    total = sum(len(v) for v in results.values())
    print(
        f"[GOV] {total} evenements - "
        f"{len(form4)} Form4, {len(contracts)} contrats, "
        f"{len(patents_us)} brevets US, {len(patents_eu)} brevets EU."
    )

    return results


async def fetch_sec_8k_for_symbol(symbol: str) -> list[dict]:
    """Helper : recupere les 8-K pour un symbole specifique."""
    async with aiohttp.ClientSession() as session:
        return await fetch_sec_8k(session, symbol)
