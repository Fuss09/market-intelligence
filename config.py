import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Telegram
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")          # FussMarketBot (crypto)
    TELEGRAM_BOURSE_CHAT_ID = os.getenv("TELEGRAM_BOURSE_CHAT_ID")  # FussBourse (stocks)

    # APIs
    NEWS_API_KEY = os.getenv("NEWS_API_KEY")
    TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL")

    # Morning brief schedule (UTC — Paris = UTC+2)
    MORNING_BRIEF_HOUR_UTC = 6      # 08h00 Paris heure d'été
    MORNING_BRIEF_MINUTE_UTC = 0

    # Scoring thresholds
    SIGNAL_SCORE_MIN = 75
    SIGNAL_COUNT_MIN = 3

    # Anthropic model
    ANTHROPIC_MODEL = "claude-sonnet-4-6"
    ANTHROPIC_MAX_TOKENS = 1000

    @classmethod
    def validate(cls):
        required = {
            "TELEGRAM_TOKEN": cls.TELEGRAM_TOKEN,
            "TELEGRAM_CHAT_ID": cls.TELEGRAM_CHAT_ID,
            "TELEGRAM_BOURSE_CHAT_ID": cls.TELEGRAM_BOURSE_CHAT_ID,
            "ANTHROPIC_API_KEY": cls.ANTHROPIC_API_KEY,
            "DATABASE_URL": cls.DATABASE_URL,
        }
        optional = {
            "NEWS_API_KEY": cls.NEWS_API_KEY,
            "TWELVE_DATA_KEY": cls.TWELVE_DATA_KEY,
            "BINANCE_API_KEY": cls.BINANCE_API_KEY,
        }

        missing_required = [k for k, v in required.items() if not v]
        missing_optional = [k for k, v in optional.items() if not v]

        if missing_required:
            raise EnvironmentError(
                f"Variables d'environnement obligatoires manquantes : {missing_required}"
            )
        if missing_optional:
            print(f"[CONFIG] Variables optionnelles non configurées : {missing_optional}")

        print("[CONFIG] Configuration validée.")
        return True
