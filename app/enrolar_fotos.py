"""
Asigna la foto (rostro) de cada empleado en los relojes donde se dio de alta
SIN biometría el 2026-07-28 (ver app/relojes_sync.py y app/data/altas_20260728.json).

Fuente de la foto: el endpoint ya existente de `ever` que sirve las fotos de
legajo por DNI (carpeta employees/, fuera de git). Como ever y hikvision-api
comparten la network docker "ai-net", se llega directo por nombre de
container: http://mangueras_ever:3000/api/rrhh/legajos/foto/<dni>.

Deliberadamente NO toca ningún (device, employee_no) que no esté en la lista
estática de altas_20260728.json: esa lista son los únicos casos garantizados
sin rostro enrolado (el UserInfo no existía en ese reloj antes de esa
corrida). No hay endpoint de "buscar si ya tiene rostro" verificado en estos
equipos, así que evitamos tocar a nadie que ya pudiera tener una cara
enrolada de antes (no queremos pisar biometría real con una foto de legajo).
"""
import io
import json
import logging
import os
from pathlib import Path

import requests

from . import db
from .config import DEVICES, EVER_FOTO_BASE_URL
from .hikvision import _post_files

log = logging.getLogger("enrolar_fotos")

DATA_FILE = Path(__file__).parent / "data" / "altas_20260728.json"
FETCH_TIMEOUT = 10


def _host_por_nombre(nombre: str) -> str | None:
    for d in DEVICES:
        if d.name == nombre:
            return d.host
    return None


def cargar_lista_objetivo() -> dict[str, list[str]]:
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _normalizar_dni(raw: str) -> str:
    return "".join(c for c in (raw or "") if c.isdigit())


def dni_por_employee_no() -> dict[str, str]:
    """employeeNo -> dni (normalizado, solo dígitos) desde everwear.legajo."""
    with db.get_conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT "employeeNo" AS emp, dni
            FROM everwear.legajo
            WHERE "employeeNo" IS NOT NULL AND dni IS NOT NULL AND TRIM(dni) <> ''
        """)
        rows = cur.fetchall()
    out: dict[str, str] = {}
    for r in rows:
        emp = str(r["emp"]).strip()
        dni = _normalizar_dni(r["dni"])
        if emp and dni:
            out[emp] = dni
    return out


def _fetch_foto(dni: str) -> bytes | None:
    url = f"{EVER_FOTO_BASE_URL.rstrip('/')}/{dni}"
    try:
        r = requests.get(url, timeout=FETCH_TIMEOUT)
        if r.status_code == 200 and r.content:
            return r.content
    except requests.RequestException as e:
        log.warning(f"fetch foto dni={dni} falló: {e}")
    return None


def _a_jpg(raw: bytes) -> bytes | None:
    """Devuelve JPEG válido. Si ya es JPEG, tal cual; si no, intenta convertir con Pillow."""
    if raw[:2] == b"\xff\xd8":
        return raw
    try:
        from PIL import Image  # import perezoso: solo si hace falta convertir
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception as e:
        log.warning(f"no se pudo convertir a JPEG: {e}")
        return None


def agregar_rostro(host: str, employee_no: str, foto_jpg: bytes) -> dict:
    """Sube el rostro a un UserInfo YA EXISTENTE en el reloj (no crea/edita UserInfo)."""
    meta = json.dumps({"faceLibType": "blackFD", "FDID": "1", "FPID": employee_no})
    files = {
        "FaceDataRecord": (None, meta, "application/json"),
        "img": (f"{employee_no}.jpg", foto_jpg, "image/jpeg"),
    }
    r = _post_files(host, "/ISAPI/Intelligent/FDLib/FDSetUp?format=json", files=files, timeout=30)
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def enrolar_fotos(dry_run: bool = True) -> dict:
    objetivo = cargar_lista_objetivo()
    dni_map = dni_por_employee_no()
    resultado: dict = {"dry_run": dry_run, "relojes": []}

    for device_name, emps in objetivo.items():
        host = _host_por_nombre(device_name)
        det: dict = {"device": device_name, "host": host, "total": len(emps),
                     "listas_para_subir": 0, "subidas_ok": 0,
                     "sin_dni": [], "sin_foto": [], "foto_no_convertible": [], "errores": []}
        if not host:
            det["error_reloj"] = "device no está en HIK_DEVICES"
            resultado["relojes"].append(det)
            continue

        for emp in emps:
            dni = dni_map.get(emp)
            if not dni:
                det["sin_dni"].append(emp)
                continue
            raw = _fetch_foto(dni)
            if not raw:
                det["sin_foto"].append({"employee_no": emp, "dni": dni})
                continue
            jpg = _a_jpg(raw)
            if not jpg:
                det["foto_no_convertible"].append({"employee_no": emp, "dni": dni})
                continue
            det["listas_para_subir"] += 1
            if not dry_run:
                try:
                    agregar_rostro(host, emp, jpg)
                    det["subidas_ok"] += 1
                except Exception as e:
                    log.error(f"[{device_name}] rostro {emp} falló: {e}")
                    det["errores"].append({"employee_no": emp, "dni": dni, "error": str(e)})

        resultado["relojes"].append(det)
    return resultado
