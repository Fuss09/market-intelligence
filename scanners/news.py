"""
Scanner news — temporairement desactive.
NewsAPI bloque par les restrictions reseau Railway.
A reactiver si acces reseau elargi ou proxy configure.

Sources actives en remplacement :
- ClinicalTrials.gov (essais cliniques)
- USASpending.gov (contrats gouvernementaux)
- SEC EDGAR Form 4 + 8-K
"""


async def run_news_scan() -> dict:
    """News scan desactive — retourne dict vide sans erreur."""
    print("[NEWS] Scanner news desactive (reseau Railway).")
    return {}


def get_news_for_symbol(news_by_sector: dict, symbol: str, sector: str) -> list[dict]:
    """Retourne liste vide — news desactivees."""
    return []
