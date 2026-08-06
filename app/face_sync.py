"""
Replica rostros (FDLib) entre relojes Hikvision.

Para cada reloj ORIGEN (en orden: primero oficina, después fabrica, después
lilser — o el orden que llegue en `orden`), detecta qué FPIDs tienen rostro
en el origen y NO en cada destino, descarga la foto del origen (faceURL de
FDSearch) y la sube al destino vía FaceDataRecord (único endpoint de alta de
rostro que acepta este firmware — FDSetUp/UserFace/FDModify devuelven
notSupport/400, ver enrolar_fotos.py).

Nunca pisa un rostro existente: si el destino ya tiene rostro para ese FPID,
se saltea. Resultado neto: la unión de rostros queda en los 3 relojes; si un
FPID tiene foto distinta en dos relojes, gana el que aparece primero en el
orden.

dry_run=True (default): solo lista qué copiaría, no descarga ni sube nada.
"""
import json
import logging
import time

import requests

from .config import DEVICES, HIK_USER, HIK_PASSWORD
from .enrolar_fotos import _a_jpg
from .hikvision import _post_files, _request

log = logging.getLogger("face_sync")

FDID = "1"
LIB = "blackFD"
PAGE = 30
MAX_PAGES = 2000
SLEEP_ENTRE_SUBIDAS = 0.15


def _listar_rostros(host: str) -> dict[str, str]:
    """FPID -> faceURL de todos los rostros del reloj (FDSearch paginado)."""
    out: dict[str, str] = {}
    pos = 0
    for _ in range(MAX_PAGES):
        body = {"searchResultPosition": pos, "maxResults": PAGE,
                "faceLibType": LIB, "FDID": FDID}
        r = _request("POST", host, "/ISAPI/Intelligent/FDLib/FDSearch?format=json",
                     data=json.dumps(body), timeout=15)
        d = r.json()
        # algunos firmwares envuelven la respuesta
        d = d.get("FDSearchResult", d)
        matches = d.get("MatchList") or []
        for m in matches:
            fpid = str(m.get("FPID", "")).strip()
            if fpid:
                out[fpid] = m.get("faceURL", "") or ""
        try:
            total = int(d.get("totalMatches") or 0)
        except (TypeError, ValueError):
            total = 0
        pos += len(matches)
        if not matches or (total and pos >= total):
            return out
        time.sleep(0.05)
    log.error(f"[{host}] paginación FDSearch abortada pos={pos}")
    return out


def _descargar_rostro(host: str, face_url: str) -> bytes:
    """Baja el JPG del faceURL (absoluto o relativo al reloj), con digest."""
    if not face_url:
        raise RuntimeError("faceURL vacío en FDSearch")
    url = face_url if face_url.startswith("http") else f"http://{host}{face_url}"
    r = requests.get(url, auth=requests.auth.HTTPDigestAuth(HIK_USER, HIK_PASSWORD),
                     timeout=20)
    r.raise_for_status()
    jpg = _a_jpg(r.content)
    if not jpg:
        raise RuntimeError(f"faceURL no devolvió imagen convertible ({len(r.content)}b)")
    return jpg


def _subir_rostro(host: str, fpid: str, jpg: bytes) -> dict:
    """FaceDataRecord al destino. Lanza con el detalle real del reloj si falla."""
    meta = json.dumps({"faceLibType": LIB, "FDID": FDID, "FPID": fpid})
    files = {
        "FaceDataRecord": (None, meta, "application/json"),
        "img": (f"{fpid}.jpg", jpg, "image/jpeg"),
    }
    try:
        r = _post_files(host, "/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json",
                        files=files, timeout=30)
    except requests.HTTPError as e:
        body = e.response.text[:300] if e.response is not None else ""
        raise RuntimeError(f"FaceDataRecord {e} :: {body}") from e
    try:
        j = r.json()
    except ValueError:
        return {"raw": r.text[:200]}
    if j.get("statusCode") not in (1, None):
        raise RuntimeError(f"FaceDataRecord rechazado: {json.dumps(j)[:300]}")
    return j


def _ordenar_devices(orden: list[str] | None):
    """Devuelve DEVICES en el orden pedido (nombres); los no mencionados van al final."""
    if not orden:
        return list(DEVICES)
    por_nombre = {d.name: d for d in DEVICES}
    out = [por_nombre[n] for n in orden if n in por_nombre]
    out += [d for d in DEVICES if d not in out]
    return out


def sync_rostros(dry_run: bool = True, orden: list[str] | None = None) -> dict:
    devices = _ordenar_devices(orden)
    resultado: dict = {"dry_run": dry_run,
                       "orden": [d.name for d in devices],
                       "rostros_por_reloj": {}, "pares": []}

    # 1. inventario de rostros por reloj
    rostros: dict[str, dict[str, str]] = {}   # device.name -> {fpid: faceURL}
    fallidos: set[str] = set()
    for dev in devices:
        try:
            rostros[dev.name] = _listar_rostros(dev.host)
            resultado["rostros_por_reloj"][dev.name] = len(rostros[dev.name])
        except Exception as e:
            log.error(f"[{dev.name}] FDSearch falló: {e}")
            resultado["rostros_por_reloj"][dev.name] = f"error: {e}"
            fallidos.add(dev.name)

    # 2. cada origen → cada destino, en orden; solo FPIDs faltantes
    cache: dict[tuple[str, str], bytes] = {}  # (origen, fpid) -> jpg
    for src in devices:
        if src.name in fallidos:
            continue
        for dst in devices:
            if dst.name == src.name or dst.name in fallidos:
                continue
            faltan = sorted(set(rostros[src.name]) - set(rostros[dst.name]))
            par: dict = {"origen": src.name, "destino": dst.name,
                         "faltantes": len(faltan), "fpids": faltan,
                         "subidos": 0, "errores": []}
            if not dry_run:
                for fpid in faltan:
                    try:
                        key = (src.name, fpid)
                        if key not in cache:
                            cache[key] = _descargar_rostro(
                                src.host, rostros[src.name][fpid])
                        _subir_rostro(dst.host, fpid, cache[key])
                        par["subidos"] += 1
                        # el destino ya lo tiene: los próximos orígenes no lo re-suben
                        rostros[dst.name][fpid] = ""
                    except Exception as e:
                        log.error(f"[{src.name}→{dst.name}] rostro {fpid}: {e}")
                        par["errores"].append({"fpid": fpid, "error": str(e)})
                    time.sleep(SLEEP_ENTRE_SUBIDAS)
            resultado["pares"].append(par)
    return resultado
