from dotenv import load_dotenv
import os
import json
from dataclasses import dataclass

load_dotenv()


@dataclass
class Device:
    host: str
    name: str


def _load_devices() -> list[Device]:
    raw = os.getenv("HIK_DEVICES", "[]")
    return [Device(**d) for d in json.loads(raw)]


class Config:
    HIK_USER: str = os.getenv("HIK_USER", "admin")
    HIK_PASSWORD: str = os.getenv("HIK_PASSWORD", "")
    HIK_DEVICES: list = _load_devices()
    CLASIF_CUTOFF_HOUR: int = int(os.getenv("CLASIF_CUTOFF_HOUR", "12"))
    CLASIF_DEVICES: set = set(
        d.strip() for d in os.getenv("CLASIF_DEVICES", "fabrica,oficina").split(",") if d.strip()
    )
    ANTHROPIC_KEY: str = os.getenv("ANTHROPIC_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://n8n_qdrant:6333")
    QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "")
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "cvs")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gpt-4.1-mini")
    TOP_K: int = int(os.getenv("TOP_K", "5"))
    CONTEXT_WINDOW: int = int(os.getenv("CONTEXT_WINDOW", "30"))
    CORS_ORIGINS: list = os.getenv("CORS", "*").split(",")
    TZ: str = os.getenv("TZ", "America/Argentina/Buenos_Aires")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    def device_by_host(self, host: str) -> "Device | None":
        return next((d for d in self.HIK_DEVICES if d.host == host), None)


config = Config()

# Alias de compatibilidad para hikvision.py
DEVICES       = config.HIK_DEVICES
HIK_USER      = config.HIK_USER
HIK_PASSWORD  = config.HIK_PASSWORD
PAGE_SIZE     = int(os.getenv("HIK_PAGE_SIZE", "30"))
MAX_RETRIES   = int(os.getenv("HIK_MAX_RETRIES", "4"))
RETRY_BACKOFF = float(os.getenv("HIK_RETRY_BACKOFF", "2.0"))
DATABASE_URL  = config.DATABASE_URL
API_TOKEN     = os.getenv("API_TOKEN", "")
CLASIF_CUTOFF_HOUR = config.CLASIF_CUTOFF_HOUR
CLASIF_DEVICES     = config.CLASIF_DEVICES