import os, sys, json, uuid, requests
from requests.auth import HTTPDigestAuth
from requests_toolbelt.multipart.encoder import MultipartEncoder

HOST = os.getenv("HIK_HOST", "10.10.0.12")
USER = os.getenv("HIK_USER", "admin")
PASS = os.getenv("HIK_PASS", "161982br")
SNAPSHOT = sys.argv[1] if len(sys.argv) > 1 else "/code/snapshots/latest.jpg"

BASE = f"http://{HOST}"
AUTH = HTTPDigestAuth(USER, PASS)
JSON_HDR = {"Content-Type": "application/json"}


def last_employee_no() -> str:
    url = f"{BASE}/ISAPI/AccessControl/UserInfo/Search?format=json"
    max_no, pos, sid = 0, 0, str(uuid.uuid4())[:8]
    while True:
        body = {"UserInfoSearchCond": {"searchID": sid, "searchResultPosition": pos, "maxResults": 30}}
        r = requests.post(url, auth=AUTH, data=json.dumps(body), headers=JSON_HDR, timeout=15)
        r.raise_for_status()
        data = r.json().get("UserInfoSearch", {})
        for u in data.get("UserInfo", []) or []:
            try:
                n = int(u.get("employeeNo", "0"))
                if n > max_no: max_no = n
            except ValueError: pass
        if data.get("responseStatusStrg") != "MORE": break
        pos += data.get("numOfMatches", 30)
    return str(max_no)


def upload_face(emp_no: str, jpg: bytes):
    url = f"{BASE}/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json"
    rec = {"faceLibType": "blackFD", "FDID": "1", "FPID": emp_no}
    enc = MultipartEncoder(fields={
        "FaceDataRecord": (None, json.dumps(rec), "application/json"),
        "img": ("face.jpg", jpg, "image/jpeg"),
    })
    r = requests.post(url, auth=AUTH, data=enc, headers={"Content-Type": enc.content_type}, timeout=30)
    r.raise_for_status()
    return r.json() if r.text else {}


if __name__ == "__main__":
    emp = last_employee_no()
    with open(SNAPSHOT, "rb") as f: jpg = f.read()
    print(f"emp={emp} bytes={len(jpg)}")
    print(upload_face(emp, jpg))
