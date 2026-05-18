# """
# Cliente Hikvision — refactor del script original.
# Sin globals de fechas; recibe rango por parámetro.
# """
# import requests
# from requests.auth import HTTPDigestAuth
# from datetime import datetime, timezone
# from concurrent.futures import ThreadPoolExecutor, as_completed
# import json
# import time
# import threading
# import xml.etree.ElementTree as ET
# import logging

# from .config import (
#     DEVICES, HIK_USER, HIK_PASSWORD,
#     PAGE_SIZE, MAX_RETRIES, RETRY_BACKOFF,
# )

# log = logging.getLogger("hikvision")
# _print_lock = threading.Lock()

# HEADERS = {"Content-Type": "application/json"}
# ACCESS_MINOR_CODES = {75, 76}  # 75=ENTRADA, 76=SALIDA


# def _new_session() -> requests.Session:
#     s = requests.Session()
#     s.auth = HTTPDigestAuth(HIK_USER, HIK_PASSWORD)
#     return s


# def _request(method: str, host: str, path: str, **kw):
#     url = f"http://{host}{path}"
#     last = None
#     for attempt in range(MAX_RETRIES):
#         try:
#             s = _new_session()
#             r = s.get(url, **kw) if method == "GET" else s.post(url, headers=HEADERS, **kw)
#             if r.status_code == 401:
#                 s = _new_session()
#                 r = s.get(url, **kw) if method == "GET" else s.post(url, headers=HEADERS, **kw)
#             r.raise_for_status()
#             return r
#         except (requests.ConnectionError, requests.Timeout) as e:
#             last = e
#             wait = RETRY_BACKOFF * (2 ** attempt)
#             with _print_lock:
#                 log.warning(f"[{host}] retry {attempt+1}/{MAX_RETRIES} ({type(e).__name__}) wait={wait:.1f}s")
#             time.sleep(wait)
#     raise last


# def _get_device_info(host: str) -> str:
#     try:
#         r = _request("GET", host, "/ISAPI/System/deviceInfo", timeout=5)
#         root = ET.fromstring(r.text)
#         ns = {"h": "http://www.hikvision.com/ver20/XMLSchema"}
#         m = root.find(".//h:model", ns)
#         return m.text if m is not None else "?"
#     except Exception as e:
#         return f"ERR:{e}"


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
#                      data=json.dumps(body), timeout=10)
#         d = r.json().get("UserInfoSearch", {})
#         lst = d.get("UserInfo", [])
#         total = d.get("totalMatches", 0)
#         for u in lst:
#             users[str(u.get("employeeNo", ""))] = u.get("name", "")
#         pos += len(lst)
#         if not lst or pos >= total:
#             break
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
#         d = r.json().get("AcsEvent", {})
#         lst = d.get("InfoList", [])
#         total = d.get("totalMatches", 0)
#         for it in lst:
#             events.append({
#                 "employee_no": str(it.get("employeeNoString", it.get("employeeNo", ""))),
#                 "name":        it.get("name", ""),
#                 "time":        it.get("time", ""),
#                 "major":       it.get("major", ""),
#                 "minor":       it.get("minor", ""),
#                 "card_no":     it.get("cardNo", ""),
#             })
#         pos += len(lst)
#         if not lst or pos >= total:
#             log.info(f"[{dname}] {len(events)} eventos descargados")
#             break
#         time.sleep(0.05)
#     return events


# def is_access_event(major, minor) -> bool:
#     mj = int(major) if str(major).isdigit() else -1
#     mn = int(minor) if str(minor).isdigit() else -1
#     return mj == 5 and mn in ACCESS_MINOR_CODES


# def event_tipo(minor) -> str | None:
#     mn = int(minor) if str(minor).isdigit() else -1
#     if mn == 75: return "ENTRADA"
#     if mn == 76: return "SALIDA"
#     return None


# def _fetch_one(dev, start_utc, end_utc) -> dict:
#     out = {"device": dev, "users": {}, "events": [], "model": "?", "error": None}
#     try:
#         out["model"] = _get_device_info(dev.host)
#         out["users"] = _get_users(dev.host)
#         log.info(f"[{dev.name}] model={out['model']} users={len(out['users'])}")
#         evs = _get_events(dev.host, dev.name, start_utc, end_utc)
#         for e in evs:
#             e["device"] = dev.name
#         out["events"] = evs
#     except Exception as e:
#         out["error"] = str(e)
#         log.error(f"[{dev.name}] {e}")
#     return out


# def fetch_all(start_utc: datetime, end_utc: datetime) -> tuple[dict, list[dict]]:
#     """Devuelve (users_global, eventos_dedup) descargando todos los relojes en paralelo."""
#     if not DEVICES:
#         raise RuntimeError("HIK_DEVICES vacío")

#     all_users: dict[str, str] = {}
#     all_events: list[dict] = []

#     with ThreadPoolExecutor(max_workers=len(DEVICES)) as pool:
#         futs = {pool.submit(_fetch_one, d, start_utc, end_utc): d for d in DEVICES}
#         for f in as_completed(futs):
#             r = f.result()
#             for k, v in r["users"].items():
#                 if v and (k not in all_users or not all_users[k]):
#                     all_users[k] = v
#             all_events.extend(r["events"])

#     # dedup (employee_no, time, device)
#     seen = {}
#     for e in all_events:
#         key = (e["employee_no"], e["time"], e["device"])
#         if key not in seen:
#             seen[key] = e
#     return all_users, list(seen.values())


"""
Cliente Hikvision — refactor del script original.
Sin globals de fechas; recibe rango por parámetro.
"""
import requests
from requests.auth import HTTPDigestAuth
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
import threading
import xml.etree.ElementTree as ET
import logging

from .config import (
    DEVICES, HIK_USER, HIK_PASSWORD,
    PAGE_SIZE, MAX_RETRIES, RETRY_BACKOFF,
)

log = logging.getLogger("hikvision")
_print_lock = threading.Lock()

HEADERS = {"Content-Type": "application/json"}
ACCESS_MINOR_CODES = {75, 76}  # 75=ENTRADA, 76=SALIDA


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
#                      data=json.dumps(body), timeout=10)
#         d = r.json().get("UserInfoSearch", {})
#         lst = d.get("UserInfo", [])
#         total = d.get("totalMatches", 0)
#         for u in lst:
#             users[str(u.get("employeeNo", ""))] = u.get("name", "")
#         pos += len(lst)
#         if not lst or pos >= total:
#             break
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
#         d = r.json().get("AcsEvent", {})
#         lst = d.get("InfoList", [])
#         total = d.get("totalMatches", 0)
#         for it in lst:
#             emp_no  = str(it.get("employeeNoString", it.get("employeeNo", "")) or "").strip()
#             card_no = str(it.get("cardNo", "") or "").strip()
#             name    = (it.get("name", "") or "").strip()
#             # employee_no vacío → sintetizar uno estable para no colapsar todos los anónimos en un único grupo
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
#         if not lst or pos >= total:
#             log.info(f"[{dname}] {len(events)} eventos descargados")
#             break
#         time.sleep(0.05)
#     return events

def _get_users(host: str) -> dict[str, str]:
    users: dict[str, str] = {}
    pos = 0
    while True:
        body = {"UserInfoSearchCond": {
            "searchID": "1",
            "searchResultPosition": pos,
            "maxResults": 500,
        }}
        r = _request("POST", host, "/ISAPI/AccessControl/UserInfo/Search?format=json",
                     data=json.dumps(body), timeout=10)
        d = r.json().get("UserInfoSearch", {})
        lst = d.get("UserInfo", [])
        status = d.get("responseStatusStrg", "")
        for u in lst:
            users[str(u.get("employeeNo", ""))] = u.get("name", "")
        pos += len(lst)
        if not lst or status != "MORE":
            break
    return users


def _get_events(host: str, dname: str, start_utc: datetime, end_utc: datetime) -> list[dict]:
    events: list[dict] = []
    pos = 0
    while True:
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
        d = r.json().get("AcsEvent", {})
        lst = d.get("InfoList", [])
        status = d.get("responseStatusStrg", "")
        for it in lst:
            emp_no  = str(it.get("employeeNoString", it.get("employeeNo", "")) or "").strip()
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
        if not lst or status != "MORE":
            log.info(f"[{dname}] {len(events)} eventos descargados")
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