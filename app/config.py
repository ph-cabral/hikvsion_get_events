"""Carga config desde env."""
import os
import json
from dataclasses import dataclass


@dataclass
class Device:
    host: str
    name: str


def _load_devices() -> list[Device]:
    """
    HIK_DEVICES como JSON:
      [{"host":"10.10.0.12","name":"oficina"}, ...]
    """
    raw = os.getenv("HIK_DEVICES", "[]")
    return [Device(**d) for d in json.loads(raw)]


HIK_USER     = os.getenv("HIK_USER", "admin")
HIK_PASSWORD = os.getenv("HIK_PASSWORD", "")
DEVICES      = _load_devices()

PAGE_SIZE      = int(os.getenv("HIK_PAGE_SIZE", "30"))
MAX_RETRIES    = int(os.getenv("HIK_MAX_RETRIES", "4"))
RETRY_BACKOFF  = float(os.getenv("HIK_RETRY_BACKOFF", "2.0"))

DATABASE_URL = os.getenv("DATABASE_URL", "")

API_TOKEN = os.getenv("API_TOKEN", "")  # opcional, si está exige header
