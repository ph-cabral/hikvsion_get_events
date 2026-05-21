"""Orquesta: descarga → normaliza → arma resumen → guarda."""
import time
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from . import hikvision, db

log = logging.getLogger("job")

TZ_AR = timezone(timedelta(hours=-3))


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def run_sync(start_ar: datetime, end_ar: datetime) -> dict:
    """
    start_ar/end_ar deben ser datetimes con tz (AR). Si vienen naive, se asume AR.
    """
    if start_ar.tzinfo is None:
        start_ar = start_ar.replace(tzinfo=TZ_AR)
    if end_ar.tzinfo is None:
        end_ar = end_ar.replace(tzinfo=TZ_AR)

    start_utc = start_ar.astimezone(timezone.utc)
    end_utc   = end_ar.astimezone(timezone.utc)

    t0 = time.time()
    job_id = db.create_job(start_utc, end_utc)
    log.info(f"job#{job_id} {start_ar} → {end_ar}")

    try:
        # users, eventos_raw = hikvision.fetch_all(start_utc, end_utc)
        # users, eventos_raw = _events_from_db(start_utc, end_utc)
        users, eventos_raw = hikvision.fetch_all(start_utc, end_utc)
        # filtrar solo entradas/salidas
        acc = []
        for e in eventos_raw:
            if not hikvision.is_access_event(e["major"], e["minor"]):
                continue
            try:
                dt = _parse_iso(e["time"])
            except Exception:
                continue
            dt_ar = dt.astimezone(TZ_AR)
            nombre = (e["name"] or users.get(e["employee_no"], "") or "").strip() or None
            acc.append({
                "device":      e["device"],
                "employee_no": e["employee_no"],
                "name":        nombre,
                "event_time":  dt,           # se guarda en UTC tz-aware
                "_dt_ar":      dt_ar,
                "major":       int(e["major"]) if str(e["major"]).isdigit() else None,
                "minor":       int(e["minor"]) if str(e["minor"]).isdigit() else None,
                "card_no":     e["card_no"] or None,
                "tipo":        hikvision.event_tipo(e["minor"]),
            })

        n_ev = db.upsert_eventos(job_id, acc)

        # resumen por (empleado, día AR)
        grupos: dict[tuple, list[dict]] = defaultdict(list)
        for e in acc:
            grupos[(e["employee_no"], e["_dt_ar"].date())].append(e)

        filas = []
        for (emp, fecha), evs in grupos.items():
            evs.sort(key=lambda x: x["_dt_ar"])
            entradas = [e for e in evs if e["tipo"] == "ENTRADA"]
            salidas  = [e for e in evs if e["tipo"] == "SALIDA"]
            ci = entradas[0]["_dt_ar"] if entradas else None
            co = salidas[-1]["_dt_ar"] if salidas else None
            minutos = int((co - ci).total_seconds() // 60) if (ci and co) else None
            devices = ",".join(sorted({e["device"] for e in evs}))
            filas.append({
                "employee_no":   emp,
                "employee_name": evs[0]["name"],
                "fecha":         fecha,
                "check_in":      ci.astimezone(timezone.utc) if ci else None,
                "check_out":     co.astimezone(timezone.utc) if co else None,
                "minutos":       minutos,
                "eventos_dia":   len(evs),
                "devices":       devices,
            })
        n_res = db.upsert_resumen(job_id, filas)

        dur = time.time() - t0
        db.finish_job(job_id, status="ok",
                      eventos_raw=len(eventos_raw), eventos_ok=n_ev,
                      resumen_filas=n_res, duracion=dur)
        return {
            "job_id": job_id, "status": "ok",
            "eventos_raw": len(eventos_raw),
            "eventos_ok": n_ev,
            "resumen_filas": n_res,
            "duracion_seg": round(dur, 2),
        }

    except Exception as e:
        dur = time.time() - t0
        log.exception(f"job#{job_id} falló")
        db.finish_job(job_id, status="error", error_msg=str(e), duracion=dur)
        raise

