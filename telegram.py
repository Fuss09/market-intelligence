import asyncio
import aiohttp
from config import Config


TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


async def send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """
    Envoie un message Telegram via HTTP direct (sans dépendance lourde).
    Retourne True si succès, False sinon.
    Découpe automatiquement les messages > 4096 caractères.
    """
    url = TELEGRAM_API_BASE.format(token=Config.TELEGRAM_TOKEN, method="sendMessage")
    chunks = _split_message(text)

    async with aiohttp.ClientSession() as session:
        for chunk in chunks:
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
            }
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        print(f"[TELEGRAM] Erreur API : {data.get('description')}")
                        return False
            except Exception as e:
                print(f"[TELEGRAM] Exception lors de l'envoi : {e}")
                return False

    return True


async def send_crypto_alert(text: str) -> bool:
    """Envoie une alerte sur le canal crypto (FussMarketBot)."""
    return await send_message(Config.TELEGRAM_CHAT_ID, text)


async def send_bourse_brief(text: str) -> bool:
    """Envoie le morning brief sur le canal bourse (FussBourse)."""
    return await send_message(Config.TELEGRAM_BOURSE_CHAT_ID, text)


async def send_system_alert(text: str) -> bool:
    """Envoie une alerte système (erreur critique) sur le canal crypto."""
    message = f"<b>SYSTEME</b>\n{text}"
    return await send_message(Config.TELEGRAM_CHAT_ID, message)


async def test_connections() -> bool:
    """
    Teste la connexion aux deux canaux Telegram.
    Appelé au démarrage pour valider la configuration.
    """
    print("[TELEGRAM] Test de connexion...")

    ok_crypto = await send_message(
        Config.TELEGRAM_CHAT_ID,
        "<b>FussMarketBot</b> — Système démarré. Canal crypto opérationnel."
    )
    ok_bourse = await send_message(
        Config.TELEGRAM_BOURSE_CHAT_ID,
        "<b>FussBourse</b> — Système démarré. Canal bourse opérationnel."
    )

    if ok_crypto and ok_bourse:
        print("[TELEGRAM] Les deux canaux sont opérationnels.")
        return True

    if not ok_crypto:
        print("[TELEGRAM] ERREUR : Canal crypto inaccessible.")
    if not ok_bourse:
        print("[TELEGRAM] ERREUR : Canal bourse inaccessible.")

    return False


def _split_message(text: str, max_length: int = 4096) -> list:
    """Découpe un message long en morceaux de max_length caractères."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while len(text) > max_length:
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    if text:
        chunks.append(text)

    return chunks
