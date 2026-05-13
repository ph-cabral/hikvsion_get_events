
"""FastAPI on-demand."""
import os
import logging
from datetime import datetime, date, timedelta
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Query, UploadFile, File
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from .jobs import TZ_AR


from . import db, jobs, persona
from .config import API_TOKEN, DEVICES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

app = FastAPI(title="Hikvision Asistencia API", version="1.0.0")
scheduler = BackgroundScheduler(timezone=TZ_AR)


def _auth(token: str | None):
    if API_TOKEN and token != API_TOKEN:
        raise HTTPException(401, "token inválido")

def _poll_recent():
    now = datetime.now(TZ_AR)
    # ventana de 30min con solape para cubrir clock drift y reintentos
    jobs.run_sync(now - timedelta(minutes=30), now)


class SyncReq(BaseModel):
    start: datetime = Field(..., description="ISO 8601 AR (-03:00). Si no llega tz, asumo AR.")
    end:   datetime
    async_mode: bool = False


@app.on_event("startup")
def _startup():
    db.init_pool()
    if os.getenv("ENABLE_SCHEDULER", "1") == "1":
        scheduler.add_job(_poll_recent, "cron",
                  hour="7-18", minute="0,15,30,45",
                  id="poll_dia", max_instances=1, coalesce=True)
        scheduler.add_job(_poll_recent, "cron",
                        hour=19, minute="0,15",
                        id="poll_cierre", max_instances=1, coalesce=True)
        scheduler.start()


@app.get("/health")
def health():
    return {"ok": True, "devices": [d.name for d in DEVICES]}


@app.post("/sync")
def sync(req: SyncReq, bg: BackgroundTasks, x_token: str | None = Header(None)):
    _auth(x_token)
    if req.end <= req.start:
        raise HTTPException(400, "end debe ser > start")
    if req.async_mode:
        bg.add_task(jobs.run_sync, req.start, req.end)
        return {"status": "queued", "start": req.start, "end": req.end}
    return jobs.run_sync(req.start, req.end)


@app.post("/sync/today")
def sync_today(x_token: str | None = Header(None)):
    """Sincroniza el día de hoy (AR)."""
    _auth(x_token)
    from .jobs import TZ_AR
    now = datetime.now(TZ_AR)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return jobs.run_sync(start, end)


@app.on_event("shutdown")
def _shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        
        
@app.get("/resumen")
def resumen(
    desde: date,
    hasta: date,
    employee_no: str | None = None,
    x_token: str | None = Header(None),
):
    _auth(x_token)
    return db.query_resumen(desde, hasta, employee_no)


@app.get("/eventos")
def eventos(
    desde: datetime,
    hasta: datetime,
    employee_no: str | None = None,
    x_token: str | None = Header(None),
):
    _auth(x_token)
    return db.query_eventos(desde, hasta, employee_no)


@app.get("/debug/raw")
def debug_raw(
    fecha: date,
    x_token: str | None = Header(None),
):
    """
    Descarga TODOS los eventos del día (sin filtrar major/minor) directo del reloj.
    No toca la DB. Sirve para auditar qué está viendo el reloj.
    """
    _auth(x_token)
    from .jobs import TZ_AR
    from . import hikvision
    from datetime import timezone, datetime as dt

    start_ar = dt.combine(fecha, dt.min.time()).replace(tzinfo=TZ_AR)
    end_ar   = dt.combine(fecha, dt.max.time()).replace(tzinfo=TZ_AR)
    users, raw = hikvision.fetch_all(start_ar.astimezone(timezone.utc),
                                      end_ar.astimezone(timezone.utc))

    # contar por (device, minor)
    from collections import Counter
    breakdown = Counter()
    for e in raw:
        breakdown[(e["device"], str(e["minor"]))] += 1

    return {
        "total_eventos": len(raw),
        "total_usuarios_en_relojes": len(users),
        "breakdown_por_device_minor": [
            {"device": d, "minor": m, "count": c}
            for (d, m), c in sorted(breakdown.items(), key=lambda x: -x[1])
        ],
        "eventos": raw,
    }


# ---------- maestro de personas ----------

@app.post("/personas/upload")
async def personas_upload(
    file: UploadFile = File(...),
    x_token: str | None = Header(None),
):
    """
    Carga/actualiza maestro de personas desde CSV o XLSX.
    Cabeceras aceptadas (case-insensitive, con o sin espacios/underscores):
      employee_no | person id | id   (obligatoria)
      nombre      | name              (obligatoria)
      departamento| department        (opcional)
      activo      | active            (opcional, default true)
    """
    _auth(x_token)
    blob = await file.read()
    fname = (file.filename or "").lower()
    try:
        if fname.endswith(".xlsx") or fname.endswith(".xlsm"):
            personas_list = persona.parse_xlsx(blob)
        else:
            # CSV: probar encodings en orden común para exports Hikvision (latin-1) y Excel (utf-8-sig)
            text = None
            for enc in ("utf-8-sig", "utf-8", "latin-1"):
                try:
                    text = blob.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                raise HTTPException(400, "no pude decodificar el CSV (probé utf-8 y latin-1)")
            personas_list = persona.parse_csv(text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    n = persona.upsert(personas_list)
    return {"upserted": n, "total_en_tabla": persona.count()}


@app.get("/personas/count")
def personas_count(x_token: str | None = Header(None)):
    _auth(x_token)
    return {"total": persona.count()}