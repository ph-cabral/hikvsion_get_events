"""
Sincroniza legajos activos (Postgres everwear.legajo) contra los usuarios
registrados en cada reloj Hikvision (DEVICES, ver config.py).

Fuente de verdad del ID: everwear.legajo.employeeNo (el mismo que usa
ever/route.ts al dar de alta y el mapeo de Anviz). Un empleado puede no
estar registrado en algún reloj porque cambió de sector/ubicación física;
este módulo detecta esos huecos y da de alta el UserInfo básico (sin foto/
biometría — eso se enrola después, físicamente, en el reloj que corresponda).

No borra ni recrea usuarios existentes en ningún reloj: solo agrega los
que faltan. Si un employeeNo ya existe en el reloj, se deja como está.
"""
import json
import logging
import time

from . import db
from .config import DEVICES
from .hikvision import _get_users, _request

log = logging.getLogger("relojes_sync")

VALID_BEGIN = "2020-01-01T00:00:00"
VALID_END = "2037-12-31T23:59:59"
SLEEP_ENTRE_ALTAS = 0.15  # no saturar el reloj con POSTs seguidos


def get_legajos_activos() -> dict[str, str]:
    """employeeNo -> nombre, solo legajos activos con employeeNo cargado."""
    with db.get_conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT "employeeNo" AS emp, nombre
            FROM everwear.legajo
            WHERE "employeeNo" IS NOT NULL AND TRIM("employeeNo") <> ''
              AND UPPER(estado) = 'ACTIVO'
        """)
        rows = cur.fetchall()
    out: dict[str, str] = {}
    for r in rows:
        emp = str(r["emp"]).strip()
        if emp:
            out[emp] = (r["nombre"] or "").strip()
    return out


def crear_usuario_basico(host: str, employee_no: str, nombre: str) -> dict:
    """Alta de UserInfo sin foto/biometría (mismo patrón que ever/route.ts)."""
    body = {"UserInfo": {
        "employeeNo": employee_no,
        "name": nombre or employee_no,
        "userType": "normal",
        "gender": "unknown",
        "Valid": {"enable": True, "beginTime": VALID_BEGIN, "endTime": VALID_END,
                  "timeType": "local"},
        "doorRight": "1",
        "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}],
    }}
    r = _request("POST", host, "/ISAPI/AccessControl/UserInfo/Record?format=json",
                 data=json.dumps(body), timeout=10)
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def diff_por_reloj() -> dict:
    """Reporte de solo-lectura: cuántos legajos activos faltan en cada reloj."""
    legajos = get_legajos_activos()
    reporte: dict = {"total_legajos_activos": len(legajos), "relojes": []}
    for dev in DEVICES:
        item: dict = {"device": dev.name, "host": dev.host}
        try:
            existentes = _get_users(dev.host)
            faltantes = {emp: nom for emp, nom in legajos.items() if emp not in existentes}
            item.update({
                "usuarios_en_reloj": len(existentes),
                "faltan": len(faltantes),
                "faltantes": [{"employee_no": e, "nombre": n}
                              for e, n in sorted(faltantes.items())],
            })
        except Exception as e:
            log.error(f"[{dev.name}] diff falló: {e}")
            item["error"] = str(e)
        reporte["relojes"].append(item)
    return reporte


def sync_faltantes(dry_run: bool = True) -> dict:
    """
    Da de alta en cada reloj los legajos activos que le faltan.
    dry_run=True (default): solo cuenta/lista, no escribe nada en los relojes.
    """
    legajos = get_legajos_activos()
    resultado: dict = {"dry_run": dry_run, "total_legajos_activos": len(legajos), "relojes": []}
    for dev in DEVICES:
        det: dict = {"device": dev.name, "host": dev.host,
                     "faltantes_detectados": 0, "creados": 0, "errores": []}
        try:
            existentes = _get_users(dev.host)
            faltantes = {emp: nom for emp, nom in legajos.items() if emp not in existentes}
            det["faltantes_detectados"] = len(faltantes)
            det["faltantes"] = [{"employee_no": e, "nombre": n}
                                for e, n in sorted(faltantes.items())]
            if not dry_run:
                for emp, nom in sorted(faltantes.items()):
                    try:
                        crear_usuario_basico(dev.host, emp, nom)
                        det["creados"] += 1
                    except Exception as e:
                        log.error(f"[{dev.name}] alta {emp} falló: {e}")
                        det["errores"].append({"employee_no": emp, "nombre": nom, "error": str(e)})
                    time.sleep(SLEEP_ENTRE_ALTAS)
        except Exception as e:
            log.error(f"[{dev.name}] sync falló: {e}")
            det["error_reloj"] = str(e)
        resultado["relojes"].append(det)
    return resultado
