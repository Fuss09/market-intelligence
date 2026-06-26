import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta


# ─── Constantes ───────────────────────────────────────────────────────────────

FDA_BASE = "https://api.fda.gov"
CLINICAL_TRIALS_BASE = "https://clinicaltrials.gov/api/v2"
EMA_BASE = "https://www.ema.europa.eu/en/medicines/search"

# Fenetre temporelle : evenements des 7 derniers jours
LOOKBACK_DAYS = 7


# ─── FDA ──────────────────────────────────────────────────────────────────────

async def fetch_fda_approvals(session: aiohttp.ClientSession) -> list[dict]:
    """
    Recupere les approbations FDA recentes (drug approvals).
    Source : openFDA API, gratuit, sans cle.
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")

    try:
        async with session.get(
            f"{FDA_BASE}/drug/drugsfda.json",
            params={
                "search": f"submissions.submission_status_date:[{cutoff}+TO+99991231]",
                "limit": 20,
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return results
            data = await resp.json()

        for item in data.get("results", []):
            sponsor = item.get("sponsor_name", "Unknown")
            app_number = item.get("application_number", "")
            products = item.get("products", [])
            submissions = item.get("submissions", [])

            # Filtre les soumissions recentes avec approbation
            for sub in submissions:
                status = sub.get("submission_status", "")
                status_date = sub.get("submission_status_date", "")
                sub_type = sub.get("submission_type", "")

                if status == "AP" and status_date >= cutoff:
                    drug_names = [p.get("brand_name", p.get("generic_name", "")) for p in products]
                    results.append({
                        "source": "FDA",
                        "type": "drug_approval",
                        "sponsor": sponsor,
                        "application": app_number,
                        "drugs": drug_names,
                        "submission_type": sub_type,
                        "date": status_date,
                        "importance": "HIGH",
                        "description": f"FDA approval {sub_type} — {sponsor} — {', '.join(drug_names[:2])}",
                    })

    except Exception as e:
        print(f"[CLINICAL] ERREUR FDA approvals : {e}")

    return results


async def fetch_fda_calendar(session: aiohttp.ClientSession) -> list[dict]:
    """
    Recupere les PDUFA dates (decisions FDA a venir) via openFDA.
    """
    results = []
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y%m%d")

    try:
        async with session.get(
            f"{FDA_BASE}/drug/drugsfda.json",
            params={
                "search": f"submissions.pdufa_date:[{today}+TO+{future}]",
                "limit": 10,
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return results
            data = await resp.json()

        for item in data.get("results", []):
            sponsor = item.get("sponsor_name", "Unknown")
            products = item.get("products", [])
            submissions = item.get("submissions", [])

            for sub in submissions:
                pdufa = sub.get("pdufa_date", "")
                if pdufa and pdufa >= today:
                    drug_names = [p.get("brand_name", p.get("generic_name", "")) for p in products]
                    results.append({
                        "source": "FDA",
                        "type": "pdufa_date",
                        "sponsor": sponsor,
                        "drugs": drug_names,
                        "pdufa_date": pdufa,
                        "importance": "HIGH",
                        "description": f"PDUFA date {pdufa} — {sponsor} — {', '.join(drug_names[:2])}",
                    })

    except Exception as e:
        print(f"[CLINICAL] ERREUR FDA calendar : {e}")

    return results


# ─── ClinicalTrials.gov ───────────────────────────────────────────────────────

async def fetch_clinical_trials(session: aiohttp.ClientSession) -> list[dict]:
    """
    Recupere les essais Phase 2 et Phase 3 avec resultats recents.
    Source : ClinicalTrials.gov API v2, gratuit.
    """
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    for phase in ["PHASE2", "PHASE3"]:
        try:
            async with session.get(
                f"{CLINICAL_TRIALS_BASE}/studies",
                params={
                    "filter.advanced": f"AREA[Phase]{phase} AND AREA[LastUpdatePostDate]RANGE[{cutoff},MAX]",
                    "fields": "NCTId,BriefTitle,LeadSponsorName,Phase,OverallStatus,LastUpdatePostDate,Condition,InterventionName",
                    "pageSize": 10,
                    "sort": "LastUpdatePostDate:desc",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()

            for study in data.get("studies", []):
                proto = study.get("protocolSection", {})
                id_module = proto.get("identificationModule", {})
                status_module = proto.get("statusModule", {})
                sponsor_module = proto.get("sponsorCollaboratorsModule", {})
                design_module = proto.get("designModule", {})
                conditions = proto.get("conditionsModule", {}).get("conditions", [])
                interventions = proto.get("armsInterventionsModule", {}).get("interventions", [])

                nct_id = id_module.get("nctId", "")
                title = id_module.get("briefTitle", "")
                sponsor = sponsor_module.get("leadSponsor", {}).get("name", "")
                status = status_module.get("overallStatus", "")
                update_date = status_module.get("lastUpdatePostDateStruct", {}).get("date", "")
                phase_info = design_module.get("phases", [phase])

                drug_names = [i.get("name", "") for i in interventions if i.get("type") == "DRUG"]

                importance = "HIGH" if phase == "PHASE3" else "MEDIUM"

                results.append({
                    "source": "ClinicalTrials.gov",
                    "type": f"clinical_trial_{phase.lower()}",
                    "nct_id": nct_id,
                    "title": title[:100],
                    "sponsor": sponsor,
                    "status": status,
                    "conditions": conditions[:3],
                    "drugs": drug_names[:3],
                    "update_date": update_date,
                    "phase": phase,
                    "importance": importance,
                    "description": f"{phase} update — {sponsor} — {', '.join(conditions[:2])}",
                    "url": f"https://clinicaltrials.gov/study/{nct_id}",
                })

        except Exception as e:
            print(f"[CLINICAL] ERREUR ClinicalTrials {phase} : {e}")

        await asyncio.sleep(0.5)

    return results


# ─── Scanner principal ────────────────────────────────────────────────────────

async def run_clinical_scan() -> list[dict]:
    """
    Scan complet : FDA approbations + PDUFA dates + ClinicalTrials Phase 2/3.
    Retourne une liste de catalyseurs tries par importance.
    """
    print("[CLINICAL] Scan FDA + ClinicalTrials...")

    async with aiohttp.ClientSession() as session:
        fda_approvals, fda_calendar, trials = await asyncio.gather(
            fetch_fda_approvals(session),
            fetch_fda_calendar(session),
            fetch_clinical_trials(session),
        )

    all_events = fda_approvals + fda_calendar + trials

    # Tri par importance puis date
    priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_events.sort(key=lambda x: priority.get(x.get("importance", "LOW"), 2))

    print(f"[CLINICAL] {len(all_events)} evenements detectes "
          f"({len(fda_approvals)} FDA approvals, "
          f"{len(fda_calendar)} PDUFA dates, "
          f"{len(trials)} essais cliniques).")

    return all_events


def match_clinical_to_stock(events: list[dict], symbol: str, company_name: str = "") -> list[dict]:
    """
    Filtre les evenements cliniques correspondant a un symbole ou nom de societe.
    Matching par nom de sponsor (approximatif).
    """
    if not company_name:
        return []

    company_lower = company_name.lower()
    matched = []

    for event in events:
        sponsor = event.get("sponsor", "").lower()
        if any(word in sponsor for word in company_lower.split() if len(word) > 3):
            matched.append(event)

    return matched
