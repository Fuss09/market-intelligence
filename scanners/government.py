import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta


# ─── Constantes ───────────────────────────────────────────────────────────────

USASPENDING_BASE = "https://api.usaspending.gov/api/v2"
LOOKBACK_DAYS = 7

# USPTO et EPO desactives — domaines bloques par Railway
# Reactiver si acces reseau elargi


# ─── SEC EDGAR Form 4 ─────────────────────────────────────────────────────────

async def fetch_sec_form4(session: aiohttp.ClientSession) -> list[dict]:
    """Recupere les Form 4 recents via SEC EDGAR."""
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
    """Recupere les 8-K recents pour un ticker specifique."""
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
    """Recupere les contrats gouvernementaux US >= 10M$ (defense, tech, R&D)."""
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


# ─── Scanner principal ────────────────────────────────────────────────────────

async def run_government_scan() -> dict:
    """
    Scan : SEC Form4 + USASpending.
    USPTO et EPO desactives (domaines bloques par Railway).
    """
    print("[GOV] Scan SEC EDGAR + USASpending...")

    async with aiohttp.ClientSession() as session:
        form4, contracts = await asyncio.gather(
            fetch_sec_form4(session),
            fetch_usa_contracts(session),
        )

    results = {
        "sec_form4": form4,
        "contracts": contracts,
        "patents_us": [],   # USPTO desactive
        "patents_eu": [],   # EPO desactive
    }

    total = len(form4) + len(contracts)
    print(f"[GOV] {total} evenements - {len(form4)} Form4, {len(contracts)} contrats.")

    return results


async def fetch_sec_8k_for_symbol(symbol: str) -> list[dict]:
    """Helper : recupere les 8-K pour un symbole specifique."""
    async with aiohttp.ClientSession() as session:
        return await fetch_sec_8k(session, symbol)
