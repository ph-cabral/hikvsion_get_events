import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Device:
    host: str
    name: str


def _load_devices() -> list[Device]:
    """HIK_DEVICES: JSON tipo [{"host":"10.0.0.1","name":"fabrica"}, ...]"""
    raw = os.getenv("HIK_DEVICES", "[]")
    try:
        parsed = json.loads(raw)
        return [Device(**d) for d in parsed]
    except (json.JSONDecodeError, TypeError) as e:
        raise RuntimeError(
            f'HIK_DEVICES inválido: {raw!r}. Formato esperado: '
            '[{"host":"10.0.0.1","name":"fabrica"}]'
        ) from e


class Config:
    HIK_USER: str = os.getenv("HIK_USER", "admin")
    HIK_PASSWORD: str = os.getenv("HIK_PASSWORD", "")
    HIK_DEVICES: list = _load_devices()
    CLASIF_CUTOFF_HOUR: int = int(os.getenv("CLASIF_CUTOFF_HOUR", "12"))
    CLASIF_DEVICES: set = set(
        d.strip() for d in os.getenv("CLASIF_DEVICES", "fabrica,oficina").split(",") if d.strip()
    )
    # ── Reloj Anviz (protocolo TCP) ──────────────────────────────────────────
    ANVIZ_ENABLED: bool = os.getenv("ANVIZ_ENABLED", "1") == "1"
    ANVIZ_IP: str = os.getenv("ANVIZ_IP", "10.10.0.147")
    ANVIZ_PORT: int = int(os.getenv("ANVIZ_PORT", "5010"))
    ANVIZ_DEVICE: str = os.getenv("ANVIZ_DEVICE", "anviz")
    TZ: str = os.getenv("TZ", "America/Argentina/Buenos_Aires")
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

# Alias Anviz
ANVIZ_ENABLED = config.ANVIZ_ENABLED
ANVIZ_IP      = config.ANVIZ_IP
ANVIZ_PORT    = config.ANVIZ_PORT
ANVIZ_DEVICE  = config.ANVIZ_DEVICE
