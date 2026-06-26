import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta


# ─── Constantes ───────────────────────────────────────────────────────────────

SEC_BASE = "https://efts.sec.gov/LATEST/search-index"
SEC_EDGAR_BASE = "https://data.sec.gov"
USASPENDING_BASE = "https://api.usaspending.gov/api/v2"
USPTO_BASE = "https://api.patentsview.org/patents/query"
EPO_BASE = "https://ops.epo.org/3.2/rest-services"

LOOKBACK_DAYS = 7


# ─── SEC EDGAR Form 4 — Insider transactions ──────────────────────────────────

async def fetch_sec_form4(session: aiohttp.ClientSession) -> list[dict]:
    """
    Recupere les Form 4 recents (insider buying) via SEC EDGAR full-text search.
    Se concentre sur les achats significatifs (pas les options automatiques).
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    try:
        async with session.get(
            "https://efts.sec.gov/LATEST/search-index?q=%22form+4%22&dateRange=custom"
            f"&startdt={cutoff}&forms=4&hits.hits._source=period_of_report,"
            "entity_name,file_date,period_of_report",
            headers={"User-Agent": "FussMarketBot contact@fuss.market"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return results
            data = await resp.json()

        hits = data.get("hits", {}).get("hits", [])
        for hit in hits[:20]:
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
                    "description": f"Form 4 depose — {entity} — {file_date}",
                    "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={entity}&type=4&dateb=&owner=include&count=5",
                })

    except Exception as e:
        print(f"[GOV] ERREUR SEC Form4 : {e}")

    return results


async def fetch_sec_8k(session: aiohttp.ClientSession, ticker: str) -> list[dict]:
    """
    Recupere les 8-K recents (annonces majeures) pour un ticker specifique.
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    try:
        async with session.get(
            f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22"
            f"&forms=8-K&dateRange=custom&startdt={cutoff}",
            headers={"User-Agent": "FussMarketBot contact@fuss.market"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return results
            data = await resp.json()

        hits = data.get("hits", {}).get("hits", [])
        for hit in hits[:5]:
            source = hit.get("_source", {})
            entity = source.get("entity_name", ticker)
            file_date = source.get("file_date", "")
            description = source.get("period_of_report", "")

            results.append({
                "source": "SEC EDGAR 8-K",
                "type": "major_announcement",
                "entity": entity,
                "ticker": ticker,
                "file_date": file_date,
                "importance": "HIGH",
                "description": f"8-K majeur — {entity} — {file_date}",
                "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K&dateb=&owner=include&count=5",
            })

    except Exception as e:
        print(f"[GOV] ERREUR SEC 8-K {ticker} : {e}")

    return results


# ─── USASpending.gov — Contrats gouvernementaux ───────────────────────────────

async def fetch_usa_contracts(session: aiohttp.ClientSession) -> list[dict]:
    """
    Recupere les contrats gouvernementaux US recents (defense, tech, sante).
    Source : USASpending.gov API, gratuit.
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # Secteurs d'interet : defense, IT, R&D
    naics_codes = [
        "541715",  # R&D sciences physiques
        "334413",  # Semi-conducteurs
        "541330",  # Services ingenierie defense
        "541511",  # Programmation informatique
        "336411",  # Fabrication aeronefs
    ]

    try:
        payload = {
            "filters": {
                "time_period": [{"start_date": cutoff, "end_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}],
                "naics_codes": naics_codes,
                "award_type_codes": ["A", "B", "C", "D"],
            },
            "fields": ["Recipient Name", "Award Amount", "Awarding Agency Name", "Description", "Action Date", "NAICS Code"],
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
                return results
            data = await resp.json()

        for award in data.get("results", []):
            recipient = award.get("Recipient Name", "Unknown")
            amount = award.get("Award Amount", 0)
            agency = award.get("Awarding Agency Name", "")
            description = award.get("Description", "")
            date = award.get("Action Date", "")

            if amount and amount >= 10_000_000:  # Contrats >= 10M$
                importance = "HIGH" if amount >= 100_000_000 else "MEDIUM"
                results.append({
                    "source": "USASpending.gov",
                    "type": "government_contract",
                    "recipient": recipient,
                    "amount_usd": amount,
                    "agency": agency,
                    "description": description[:100] if description else f"Contrat {agency}",
                    "date": date,
                    "importance": importance,
                    "description_full": f"Contrat ${amount:,.0f} — {recipient} — {agency}",
                })

    except Exception as e:
        print(f"[GOV] ERREUR USASpending : {e}")

    return results


# ─── USPTO — Brevets US ───────────────────────────────────────────────────────

async def fetch_uspto_patents(session: aiohttp.ClientSession, keywords: list[str]) -> list[dict]:
    """
    Recupere les brevets recents par mots-cles via PatentsView API.
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    query_terms = " OR ".join([f'"{k}"' for k in keywords[:3]])

    try:
        payload = {
            "q": {"_and": [
                {"_gte": {"patent_date": cutoff}},
                {"_text_any": {"patent_title": query_terms}},
            ]},
            "f": ["patent_id", "patent_title", "patent_date", "assignee_organization", "patent_abstract"],
            "o": {"per_page": 10, "sort": [{"patent_date": "desc"}]},
        }

        async with session.post(
            USPTO_BASE,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return results
            data = await resp.json()

        for patent in data.get("patents", []) or []:
            title = patent.get("patent_title", "")
            date = patent.get("patent_date", "")
            assignees = patent.get("assignees", [{}])
            org = assignees[0].get("assignee_organization", "Unknown") if assignees else "Unknown"
            patent_id = patent.get("patent_id", "")

            results.append({
                "source": "USPTO PatentsView",
                "type": "patent_us",
                "patent_id": patent_id,
                "title": title[:100],
                "organization": org,
                "date": date,
                "importance": "MEDIUM",
                "description": f"Brevet US — {org} — {title[:60]}",
                "url": f"https://patents.google.com/patent/US{patent_id}",
            })

    except Exception as e:
        print(f"[GOV] ERREUR USPTO : {e}")

    return results


# ─── EPO — Brevets Europe ─────────────────────────────────────────────────────

async def fetch_epo_patents(session: aiohttp.ClientSession, keywords: list[str]) -> list[dict]:
    """
    Recupere les brevets europeens recents via EPO Open Patent Services.
    API publique, sans cle pour la recherche de base.
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    query = " OR ".join(keywords[:3])

    try:
        async with session.get(
            f"{EPO_BASE}/published-data/search/biblio",
            params={
                "q": f"ti=({query}) AND pd>={cutoff}",
                "Range": "1-5",
            },
            headers={
                "Accept": "application/json",
                "X-OPS-OAuth-consumer-key": "None",
            },
            timeout=aiohttp.ClientTimeout(total=15),
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

        for entry in entries[:5]:
            doc = entry.get("exchange-document", {})
            if isinstance(doc, list):
                doc = doc[0]

            biblio = doc.get("bibliographic-data", {})
            titles = biblio.get("invention-title", [])
            if isinstance(titles, dict):
                titles = [titles]

            title = next((t.get("$", "") for t in titles if t.get("@lang") == "en"), "")
            if not title and titles:
                title = titles[0].get("$", "")

            pub_ref = biblio.get("publication-reference", {})
            doc_id = pub_ref.get("document-id", {})
            if isinstance(doc_id, list):
                doc_id = doc_id[0]
            pub_number = doc_id.get("doc-number", {}).get("$", "")
            pub_date = doc_id.get("date", {}).get("$", "")

            parties = biblio.get("parties", {})
            applicants = parties.get("applicants", {}).get("applicant", [])
            if isinstance(applicants, dict):
                applicants = [applicants]
            org = ""
            for app in applicants:
                name = app.get("applicant-name", {}).get("name", {})
                if isinstance(name, dict):
                    org = name.get("$", "")
                    break

            if title:
                results.append({
                    "source": "EPO Open Patent Services",
                    "type": "patent_eu",
                    "patent_number": pub_number,
                    "title": title[:100],
                    "organization": org,
                    "date": pub_date,
                    "importance": "MEDIUM",
                    "description": f"Brevet EP — {org} — {title[:60]}",
                })

    except Exception as e:
        print(f"[GOV] ERREUR EPO : {e}")

    return results


# ─── Scanner principal ────────────────────────────────────────────────────────

PATENT_KEYWORDS = [
    "artificial intelligence",
    "semiconductor",
    "quantum computing",
    "defense",
    "cybersecurity",
    "nuclear",
    "robotics",
]


async def run_government_scan() -> dict:
    """
    Scan complet : SEC Form 4, 8-K, USASpending, USPTO, EPO.
    Retourne un dict structure par type de source.
    """
    print("[GOV] Scan sources reglementaires et gouvernementales...")

    async with aiohttp.ClientSession() as session:
        form4, contracts, patents_us, patents_eu = await asyncio.gather(
            fetch_sec_form4(session),
            fetch_usa_contracts(session),
            fetch_uspto_patents(session, PATENT_KEYWORDS),
            fetch_epo_patents(session, PATENT_KEYWORDS),
        )

    results = {
        "sec_form4": form4,
        "contracts": contracts,
        "patents_us": patents_us,
        "patents_eu": patents_eu,
    }

    total = sum(len(v) for v in results.values())
    print(
        f"[GOV] {total} evenements detectes "
        f"({len(form4)} Form4, {len(contracts)} contrats, "
        f"{len(patents_us)} brevets US, {len(patents_eu)} brevets EU)."
    )

    return results


async def fetch_sec_8k_for_symbol(symbol: str) -> list[dict]:
    """Helper : recupere les 8-K pour un symbole specifique."""
    async with aiohttp.ClientSession() as session:
        return await fetch_sec_8k(session, symbol)
