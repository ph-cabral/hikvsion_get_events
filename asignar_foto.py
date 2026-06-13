"""
Utilidad CLI: asigna una foto al último empleado dado de alta en el reloj.
Uso: HIK_HOST=10.10.0.12 HIK_USER=admin HIK_PASS=... python asignar_foto.py [foto.jpg]
"""
import json
import os
import sys
import uuid

import requests
from requests.auth import HTTPDigestAuth

HOST = os.getenv("HIK_HOST", "10.10.0.12")
USER = os.getenv("HIK_USER", "admin")
PASS = os.getenv("HIK_PASS", "")
SNAPSHOT = sys.argv[1] if len(sys.argv) > 1 else "/code/snapshots/latest.jpg"

if not PASS:
    sys.exit("falta HIK_PASS en el entorno (no hardcodear credenciales)")

BASE = f"http://{HOST}"
AUTH = HTTPDigestAuth(USER, PASS)
JSON_HDR = {"Content-Type": "application/json"}


def last_employee_no() -> str:
    url = f"{BASE}/ISAPI/AccessControl/UserInfo/Search?format=json"
    max_no, pos, sid = 0, 0, str(uuid.uuid4())[:8]
    for _ in range(5000):  # tope anti-loop
        body = {"UserInfoSearchCond": {"searchID": sid, "searchResultPosition": pos, "maxResults": 30}}
        r = requests.post(url, auth=AUTH, data=json.dumps(body), headers=JSON_HDR, timeout=15)
        r.raise_for_status()
        data = r.json().get("UserInfoSearch", {})
        for u in data.get("UserInfo", []) or []:
            try:
                n = int(u.get("employeeNo", "0"))
                if n > max_no:
                    max_no = n
            except ValueError:
                pass
        if data.get("responseStatusStrg") != "MORE":
            break
        pos += data.get("numOfMatches", 30)
    return str(max_no)


def upload_face(emp_no: str, jpg: bytes):
    url = f"{BASE}/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json"
    rec = {"faceLibType": "blackFD", "FDID": "1", "FPID": emp_no}
    files = {
        "FaceDataRecord": (None, json.dumps(rec), "application/json"),
        "img": ("face.jpg", jpg, "image/jpeg"),
    }
    r = requests.post(url, auth=AUTH, files=files, timeout=30)
    r.raise_for_status()
    return r.json() if r.text else {}


if __name__ == "__main__":
    emp = last_employee_no()
    if emp == "0":
        sys.exit("no encontré empleados en el reloj")
    with open(SNAPSHOT, "rb") as f:
        jpg = f.read()
    print(f"emp={emp} bytes={len(jpg)}")
    print(upload_face(emp, jpg))
