"""FastAPI on-demand."""
import logging
import os
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import anviz_poller as anviz
from . import db, enrolar_fotos, face_sync, hikvision, jobs, persona, relojes_sync
from .config import ANVIZ_DEVICE, ANVIZ_ENABLED, API_TOKEN, DEVICES
from .jobs import TZ_AR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("main")

# Rango máximo aceptado por /sync para evitar descargas accidentales gigantes.
MAX_SYNC_DAYS = int(os.getenv("MAX_SYNC_DAYS", "366"))

scheduler = BackgroundScheduler(timezone=TZ_AR)


def _poll_recent():
    now = datetime.now(TZ_AR)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        jobs.run_sync(start, now)
    except Exception:
        log.exception("poll hikvision falló")
    if ANVIZ_ENABLED:
        try:
            anviz.poll(start, now)
        except Exception:
            logging.getLogger("anviz").exception("poll anviz falló")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_pool()
    if not API_TOKEN:
        log.warning("API_TOKEN vacío: la API queda SIN autenticación. Definilo en .env para producción.")
    if os.getenv("ENABLE_SCHEDULER", "1") == "1":
        # 10 consultas por hora (~cada 6 min) de 7 a 19h. Antes eran 4/hora
        # (cada 15 min) 7-18h + 2 sueltas a las 19h; se unificó en un solo
        # job con el mismo ritmo en todo el rango (2026-08-13, pedido de Pablo).
        scheduler.add_job(_poll_recent, "cron",
                          hour="7-19", minute="0,6,12,18,24,30,36,42,48,54",
                          id="poll_dia", max_instances=1, coalesce=True)
        scheduler.start()
    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        db.close_pool()


app = FastAPI(title="Hikvision Asistencia API", version="1.1.0", lifespan=lifespan)


def _auth(token: str | None):
    if API_TOKEN and token != API_TOKEN:
        raise HTTPException(401, "token inválido")


class SyncReq(BaseModel):
    start: datetime = Field(..., description="ISO 8601 AR (-03:00). Si no llega tz, asumo AR.")
    end:   datetime
    async_mode: bool = False


@app.get("/health")
def health():
    return {
        "ok": True,
        "db": db.ping(),
        "devices": [d.name for d in DEVICES],
        "anviz": {"enabled": ANVIZ_ENABLED, "device": ANVIZ_DEVICE},
        "scheduler": scheduler.running,
    }


def _run_sync_http(start: datetime, end: datetime) -> dict:
    """Ejecuta el sync traduciendo errores a códigos HTTP útiles."""
    try:
        return jobs.run_sync(start, end)
    except hikvision.DeviceError as e:
        raise HTTPException(502, f"relojes inaccesibles: {e}")
    except Exception as e:
        log.exception("sync falló")
        raise HTTPException(500, f"sync falló: {e}")


@app.post("/sync")
def sync(req: SyncReq, bg: BackgroundTasks, x_token: str | None = Header(None)):
    _auth(x_token)
    if req.end <= req.start:
        raise HTTPException(400, "end debe ser > start")
    if (req.end - req.start) > timedelta(days=MAX_SYNC_DAYS):
        raise HTTPException(400, f"rango demasiado grande (máx {MAX_SYNC_DAYS} días); usá app.backfill para histórico")
    if req.async_mode:
        bg.add_task(jobs.run_sync, req.start, req.end)
        return {"status": "queued", "start": req.start, "end": req.end}
    return _run_sync_http(req.start, req.end)


@app.post("/sync/today")
def sync_today(x_token: str | None = Header(None)):
    """Sincroniza el día de hoy (AR)."""
    _auth(x_token)
    now = datetime.now(TZ_AR)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return _run_sync_http(start, end)


@app.post("/sync/anviz")
def sync_anviz(x_token: str | None = Header(None)):
    """Descarga los fichajes del reloj Anviz y los inserta en asistencia.evento."""
    _auth(x_token)
    res = anviz.poll()
    if res.get("status") == "error":
        raise HTTPException(502, res.get("error", "anviz falló"))
    return res


@app.get("/resumen")
def resumen(
    desde: date,
    hasta: date,
    employee_no: str | None = None,
    x_token: str | None = Header(None),
):
    _auth(x_token)
    if hasta < desde:
        raise HTTPException(400, "hasta debe ser >= desde")
    return db.query_resumen(desde, hasta, employee_no)


@app.get("/eventos")
def eventos(
    desde: datetime,
    hasta: datetime,
    employee_no: str | None = None,
    limit: int | None = None,
    x_token: str | None = Header(None),
):
    _auth(x_token)
    if hasta < desde:
        raise HTTPException(400, "hasta debe ser >= desde")
    return db.query_eventos(desde, hasta, employee_no, limit=limit)


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
    start_ar = datetime.combine(fecha, datetime.min.time()).replace(tzinfo=TZ_AR)
    end_ar = datetime.combine(fecha, datetime.max.time()).replace(tzinfo=TZ_AR)
    try:
        users, raw = hikvision.fetch_all(start_ar.astimezone(timezone.utc),
                                         end_ar.astimezone(timezone.utc))
    except hikvision.DeviceError as e:
        raise HTTPException(502, f"relojes inaccesibles: {e}")

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


@app.get("/debug/anviz")
def debug_anviz(x_token: str | None = Header(None)):
    """
    Lee el buffer del reloj Anviz SIN guardar en DB. Agrupa por anviz_id (el ID
    interno del reloj) mostrando cantidad de fichadas, primera/última fecha y si
    ya está vinculado a un legajo. Sirve para ver qué IDs faltan mapear en
    everwear.legajo.anvizId antes de correr /sync/anviz.
    """
    _auth(x_token)
    try:
        return anviz.debug_raw()
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.get("/personas/count")
def personas_count(x_token: str | None = Header(None)):
    _auth(x_token)
    return {"total": persona.count()}


@app.get("/relojes/diff")
def relojes_diff(x_token: str | None = Header(None)):
    """
    Solo lectura: compara everwear.legajo (activos) contra los usuarios
    ya cargados en cada reloj Hikvision y lista quién falta en cuál.
    No escribe nada.
    """
    _auth(x_token)
    try:
        return relojes_sync.diff_por_reloj()
    except Exception as e:
        log.exception("relojes/diff falló")
        raise HTTPException(500, f"relojes/diff falló: {e}")


class SyncRelojesReq(BaseModel):
    dry_run: bool = True


@app.post("/relojes/sync")
def relojes_sync_endpoint(req: SyncRelojesReq, x_token: str | None = Header(None)):
    """
    Da de alta (UserInfo básico, sin foto) en cada reloj los legajos activos
    que le falten. dry_run=true (default) solo reporta, no escribe.
    Idempotente: nunca toca usuarios que ya existen en el reloj.
    """
    _auth(x_token)
    try:
        return relojes_sync.sync_faltantes(dry_run=req.dry_run)
    except Exception as e:
        log.exception("relojes/sync falló")
        raise HTTPException(500, f"relojes/sync falló: {e}")


class EnrolarFotosReq(BaseModel):
    dry_run: bool = True


@app.post("/relojes/enrolar-fotos")
def relojes_enrolar_fotos(req: EnrolarFotosReq, x_token: str | None = Header(None)):
    """
    Sube la foto de legajo (vía endpoint de ever, por DNI) como rostro en
    Hikvision (FDSetUp) para los (device, employee_no) dados de alta el
    2026-07-28 sin biometría — ver app/data/altas_20260728.json.
    No toca a nadie fuera de esa lista. dry_run=true (default) solo reporta
    quién tiene foto disponible y lista para subir, sin escribir nada.
    """
    _auth(x_token)
    try:
        return enrolar_fotos.enrolar_fotos(dry_run=req.dry_run)
    except Exception as e:
        log.exception("relojes/enrolar-fotos falló")
        raise HTTPException(500, f"relojes/enrolar-fotos falló: {e}")


class SyncRostrosReq(BaseModel):
    dry_run: bool = True
    orden: list[str] | None = None  # ej: ["oficina","fabrica","lilser"]
    crear_usuarios: bool = False  # crea UserInfo en destino si no existe (employeeNoNotExist)


@app.post("/relojes/sync-rostros")
def relojes_sync_rostros(req: SyncRostrosReq, x_token: str | None = Header(None)):
    """
    Replica rostros entre relojes: para cada origen (en `orden`, default el
    orden de HIK_DEVICES) sube a los demás relojes los rostros que les
    falten. Nunca pisa un rostro existente (unión de rostros en los 3).
    dry_run=true (default) solo lista qué copiaría.
    """
    _auth(x_token)
    try:
        return face_sync.sync_rostros(dry_run=req.dry_run, orden=req.orden,
                                      crear_usuarios=req.crear_usuarios)
    except Exception as e:
        log.exception("relojes/sync-rostros falló")
        raise HTTPException(500, f"relojes/sync-rostros falló: {e}")
