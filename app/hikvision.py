"""
Cliente Hikvision — refactor del script original.
Sin globals de fechas; recibe rango por parámetro.
"""
import requests
from requests.auth import HTTPDigestAuth
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import json
import time
import threading
import xml.etree.ElementTree as ET
import logging
# app/hikvision.py
import requests, tempfile, os
from requests.auth import HTTPDigestAuth
from .config import config

            
            
from .config import (
    DEVICES, HIK_USER, HIK_PASSWORD,
    PAGE_SIZE, MAX_RETRIES, RETRY_BACKOFF,
)

log = logging.getLogger("hikvision")
_print_lock = threading.Lock()

HEADERS = {"Content-Type": "application/json"}
ACCESS_MINOR_CODES = {75, 76}  # 75=ENTRADA, 76=SALIDA

def capturar_y_enrolar(ip_reloj_camara: str, employee_no: str, nombre: str):
    auth = HTTPDigestAuth(config.HIK_USER, config.HIK_PASS)
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    try:
        r = requests.get(
            f"http://{ip_reloj_camara}/ISAPI/Streaming/channels/101/picture",
            auth=auth, timeout=10)
        r.raise_for_status()
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)

        with open(tmp, "rb") as img:
            requests.post(
                f"http://{ip_reloj_camara}/ISAPI/Intelligent/FDLib/FDSetUp?format=json",
                auth=auth, timeout=15,
                files={
                    "FaceDataRecord": (None,
                        '{"faceLibType":"blackFD","FDID":"1","FPID":"%s"}' % employee_no,
                        "application/json"),
                    "img": ("face.jpg", img, "image/jpeg"),
                }).raise_for_status()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def _new_session() -> requests.Session:
    s = requests.Session()
    s.auth = HTTPDigestAuth(HIK_USER, HIK_PASSWORD)
    return s


def _request(method: str, host: str, path: str, **kw):
    url = f"http://{host}{path}"
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            s = _new_session()
            r = s.get(url, **kw) if method == "GET" else s.post(url, headers=HEADERS, **kw)
            if r.status_code == 401:
                s = _new_session()
                r = s.get(url, **kw) if method == "GET" else s.post(url, headers=HEADERS, **kw)
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            wait = RETRY_BACKOFF * (2 ** attempt)
            with _print_lock:
                log.warning(f"[{host}] retry {attempt+1}/{MAX_RETRIES} ({type(e).__name__}) wait={wait:.1f}s")
            time.sleep(wait)
    raise last


def _get_device_info(host: str) -> str:
    try:
        r = _request("GET", host, "/ISAPI/System/deviceInfo", timeout=5)
        root = ET.fromstring(r.text)
        ns = {"h": "http://www.hikvision.com/ver20/XMLSchema"}
        m = root.find(".//h:model", ns)
        return m.text if m is not None else "?"
    except Exception as e:
        return f"ERR:{e}"
# def _get_users(host: str) -> dict[str, str]:
#     users: dict[str, str] = {}
#     pos = 0
#     while True:
#         body = {"UserInfoSearchCond": {
#             "searchID": "1",
#             "searchResultPosition": pos,
#             "maxResults": 500,
#         }}
#         r = _request("POST", host, "/ISAPI/AccessControl/UserInfo/Search?format=json",
#                      data=json.dumps(body), timeout=15)
#         r.encoding = "utf-8"
#         d = r.json().get("UserInfoSearch", {})
#         lst = d.get("UserInfo", [])
#         status = d.get("responseStatusStrg", "")
#         for u in lst:
#             users[str(u.get("employeeNo", ""))] = u.get("name", "")
#         pos += len(lst)
#         if not lst or status != "MORE":
#             break
#     return users

# def _get_users(host: str) -> dict[str, str]:
#     users: dict[str, str] = {}
#     pos = 0
#     PAGE = 30  # 500 a veces hace timeout/trunca en algunos FW
#     while True:
#         body = {"UserInfoSearchCond": {
#             "searchID": "1",
#             "searchResultPosition": pos,
#             "maxResults": PAGE,
#         }}
#         r = _request("POST", host, "/ISAPI/AccessControl/UserInfo/Search?format=json",
#                      data=json.dumps(body), timeout=15)
#         d = r.json().get("UserInfoSearch", {})
#         lst = d.get("UserInfo", [])
#         status = d.get("responseStatusStrg", "")
#         for u in lst:
#             users[str(u.get("employeeNo", ""))] = u.get("name", "")
#         if not lst or status == "NO MATCH" or len(lst) < PAGE:
#             break
#         pos += len(lst)
#         time.sleep(0.05)
#     return users


# def _get_events(host: str, dname: str, start_utc: datetime, end_utc: datetime) -> list[dict]:
#     events: list[dict] = []
#     pos = 0
#     while True:
#         body = {"AcsEventCond": {
#             "searchID": "1",
#             "searchResultPosition": pos,
#             "maxResults": PAGE_SIZE,
#             "major": 0,
#             "minor": 0,
#             "startTime": start_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
#             "endTime":   end_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
#         }}
#         r = _request("POST", host, "/ISAPI/AccessControl/AcsEvent?format=json",
#                      data=json.dumps(body), timeout=15)
#         r.encoding = "utf-8"
#         d = r.json().get("AcsEvent", {})
#         lst = d.get("InfoList", [])
#         status = d.get("responseStatusStrg", "")
#         for it in lst:
#             emp_no  = str(it.get("employeeNoString", it.get("employeeNo", "")) or "").strip()
#             card_no = str(it.get("cardNo", "") or "").strip()
#             name    = (it.get("name", "") or "").strip()
#             if not emp_no:
#                 emp_no = f"UNK-{card_no}" if card_no else f"UNK-{dname}-{it.get('time','')}"
#             events.append({
#                 "employee_no": emp_no,
#                 "name":        name,
#                 "time":        it.get("time", ""),
#                 "major":       it.get("major", ""),
#                 "minor":       it.get("minor", ""),
#                 "card_no":     card_no,
#             })
#         pos += len(lst)
#         if not lst or len(lst) < PAGE_SIZE:
#             log.info(f"[{dname}] {len(events)} eventos descargados")
#             break
#         time.sleep(0.05)
#     return events

def _get_users(host: str) -> dict[str, str]:
    users: dict[str, str] = {}
    pos = 0
    PAGE = 30
    guard = 0
    while True:
        guard += 1
        if guard > 5000:
            log.error(f"[{host}] paginación usuarios abortada pos={pos}")
            break
        body = {"UserInfoSearchCond": {
            "searchID": "1",
            "searchResultPosition": pos,
            "maxResults": PAGE,
        }}
        r = _request("POST", host, "/ISAPI/AccessControl/UserInfo/Search?format=json",
                     data=json.dumps(body), timeout=15)
        d = r.json().get("UserInfoSearch", {})
        lst = d.get("UserInfo", [])
        status = (d.get("responseStatusStrg") or "").upper()
        for u in lst:
            users[str(u.get("employeeNo", ""))] = u.get("name", "")
        pos += len(lst)
        # cortar solo cuando el reloj dice que no hay más
        if not lst or status != "MORE":
            break
        time.sleep(0.05)
    return users


def _get_events(host: str, dname: str, start_utc: datetime, end_utc: datetime) -> list[dict]:
    events: list[dict] = []
    pos = 0
    guard = 0
    while True:
        guard += 1
        if guard > 5000:
            log.error(f"[{dname}] paginación eventos abortada pos={pos}")
            break
        body = {"AcsEventCond": {
            "searchID": "1",
            "searchResultPosition": pos,
            "maxResults": PAGE_SIZE,
            "major": 0,
            "minor": 0,
            "startTime": start_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "endTime":   end_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        }}
        r = _request("POST", host, "/ISAPI/AccessControl/AcsEvent?format=json",
                     data=json.dumps(body), timeout=15)
        r.encoding = "utf-8"
        d = r.json().get("AcsEvent", {})
        lst = d.get("InfoList", [])
        status = (d.get("responseStatusStrg") or "").upper()
        for it in lst:
            # employeeNoString puede venir "" → caer a employeeNo
            emp_no  = str(it.get("employeeNoString") or it.get("employeeNo") or "").strip()
            card_no = str(it.get("cardNo", "") or "").strip()
            name    = (it.get("name", "") or "").strip()
            if not emp_no:
                emp_no = f"UNK-{card_no}" if card_no else f"UNK-{dname}-{it.get('time','')}"
            events.append({
                "employee_no": emp_no,
                "name":        name,
                "time":        it.get("time", ""),
                "major":       it.get("major", ""),
                "minor":       it.get("minor", ""),
                "card_no":     card_no,
            })
        pos += len(lst)
        # NO cortar por len(lst) < PAGE_SIZE: un reloj lento puede devolver página corta con MORE
        if not lst or status != "MORE":
            log.info(f"[{dname}] {len(events)} eventos (status={status or '?'})")
            break
        time.sleep(0.05)
    return events

def is_access_event(major, minor) -> bool:
    mj = int(major) if str(major).isdigit() else -1
    mn = int(minor) if str(minor).isdigit() else -1
    return mj == 5 and mn in ACCESS_MINOR_CODES


def event_tipo(minor) -> str | None:
    mn = int(minor) if str(minor).isdigit() else -1
    if mn == 75: return "ENTRADA"
    if mn == 76: return "SALIDA"
    return None


def _fetch_one(dev, start_utc, end_utc) -> dict:
    out = {"device": dev, "users": {}, "events": [], "model": "?", "error": None}
    try:
        out["model"] = _get_device_info(dev.host)
        out["users"] = _get_users(dev.host)
        log.info(f"[{dev.name}] model={out['model']} users={len(out['users'])}")
        evs = _get_events(dev.host, dev.name, start_utc, end_utc)
        for e in evs:
            e["device"] = dev.name
        out["events"] = evs
    except Exception as e:
        out["error"] = str(e)
        log.error(f"[{dev.name}] {e}")
    return out


def fetch_all(start_utc: datetime, end_utc: datetime) -> tuple[dict, list[dict]]:
    """Devuelve (users_global, eventos_dedup) descargando todos los relojes en paralelo."""
    if not DEVICES:
        raise RuntimeError("HIK_DEVICES vacío")

    all_users: dict[str, str] = {}
    all_events: list[dict] = []

    with ThreadPoolExecutor(max_workers=len(DEVICES)) as pool:
        futs = {pool.submit(_fetch_one, d, start_utc, end_utc): d for d in DEVICES}
        for f in as_completed(futs):
            r = f.result()
            for k, v in r["users"].items():
                if v and (k not in all_users or not all_users[k]):
                    all_users[k] = v
            all_events.extend(r["events"])

    # dedup (employee_no, time, device)
    seen = {}
    for e in all_events:
        key = (e["employee_no"], e["time"], e["device"])
        if key not in seen:
            seen[key] = e
    return all_users, list(seen.values())

def crear_empleado_con_foto(host: str, emp_no: str, nombre: str,
                            foto_bytes: bytes,
                            depto: str = "1",
                            begin="2024-01-01T00:00:00",
                            end="2030-12-31T23:59:59") -> dict:
    """Crea UserInfo + carga rostro en FDLib con reintentos."""
    import time

    # 1. UserInfo
    body = {"UserInfo": {
        "employeeNo": emp_no,
        "name": nombre,
        "userType": "normal",
        "Valid": {"enable": True, "beginTime": begin, "endTime": end,
                  "timeType": "local"},
        "doorRight": "1",
        "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}],
    }}
    _request("POST", host, "/ISAPI/AccessControl/UserInfo/Record?format=json",
             data=json.dumps(body), timeout=10)

    # 2. Validar JPG
    if not (foto_bytes[:2] == b"\xff\xd8" and len(foto_bytes) > 10_000):
        raise ValueError(f"JPG inválido: {len(foto_bytes)}b header={foto_bytes[:4]!r}")
    log.info(f"[{host}] subiendo foto emp={emp_no} bytes={len(foto_bytes)}")

    time.sleep(0.5)

    # 3. Subir rostro con 3 reintentos
    meta = json.dumps({"faceLibType": "blackFD", "FDID": "1", "FPID": emp_no})
    url = f"http://{host}/ISAPI/Intelligent/FDLib/FDSetUp?format=json"
    last_err = None
    for intento in range(3):
        s = _new_session()
        files = {
            "FaceDataRecord": (None, meta, "application/json"),
            "img": (f"{emp_no}.jpg", foto_bytes, "image/jpeg"),
        }
        try:
            r = s.post(url, files=files, timeout=30,
                       headers={"Connection": "close"})
            if r.status_code == 401:
                s = _new_session()
                r = s.post(url, files=files, timeout=30,
                           headers={"Connection": "close"})
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            last_err = e
            log.warning(f"[{host}] FDSetUp intento {intento+1}/3 falló: {e}")
            time.sleep(1.5 * (intento + 1))

    raise RuntimeError(f"FDSetUp falló tras 3 intentos: {last_err}")



def capturar_rostro(host: str, infrared: bool = False, timeout: int = 35) -> bytes:
    """Dispara la cámara del DS-K1T343M y devuelve el JPG capturado.
    Bloqueante: requiere una cara frente al equipo dentro del timeout."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CaptureFaceDataCond version="2.0" '
        'xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f'<captureInfrared>{str(infrared).lower()}</captureInfrared>'
        '<dataType>binary</dataType>'
        '</CaptureFaceDataCond>'
    )
    url = f"http://{host}/ISAPI/AccessControl/CaptureFaceData"
    s = _new_session()
    r = s.post(url, data=body, headers={"Content-Type": "application/xml"},
               timeout=timeout)
    if r.status_code == 401:
        s = _new_session()
        r = s.post(url, data=body, headers={"Content-Type": "application/xml"},
                   timeout=timeout)
    r.raise_for_status()
    return _jpg_de_multipart(r.content)


def _jpg_de_multipart(raw: bytes) -> bytes:
    """Extrae el JPG de la respuesta multipart de CaptureFaceData."""
    soi = raw.find(b"\xff\xd8")
    eoi = raw.rfind(b"\xff\xd9")
    if soi == -1 or eoi == -1:
        # status sin imagen: parsear motivo del XML
        txt = raw.decode("utf-8", "ignore")
        m = re.search(r"<statusString>(.*?)</statusString>", txt)
        raise RuntimeError(f"captura sin rostro: {m.group(1) if m else txt[:200]}")
    return raw[soi:eoi + 2]


def alta_empleado_flujo(host, emp_no, nombre, depto="1"):
    # 1. crear UserInfo (sin foto)
    body = {"UserInfo": {
        "employeeNo": emp_no, "name": nombre, "userType": "normal",
        "Valid": {"enable": True, "beginTime": "2024-01-01T00:00:00",
                  "endTime": "2030-12-31T23:59:59", "timeType": "local"},
        "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}],
    }}
    _request("POST", host, "/ISAPI/AccessControl/UserInfo/Record?format=json",
             data=json.dumps(body), timeout=10)

    # 2. capturar rostro con la cámara del reloj (persona enfrente, 35s)
    jpg = capturar_rostro(host)

    # 3. vincular rostro al empleado
    meta = json.dumps({"faceLibType": "blackFD", "FDID": "1", "FPID": emp_no})
    s = _new_session()
    url = f"http://{host}/ISAPI/Intelligent/FDLib/FDSetUp?format=json"
    files = {"FaceDataRecord": (None, meta, "application/json"),
             "img": ("face.jpg", jpg, "image/jpeg")}
    rr = s.post(url, files=files, timeout=20)
    if rr.status_code == 401:
        s = _new_session()
        rr = s.post(url, files=files, timeout=20)
    rr.raise_for_status()
    return rr.json()