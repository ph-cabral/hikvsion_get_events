"""Capa Postgres con psycopg3 + pool."""
import logging
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import DATABASE_URL

log = logging.getLogger("db")

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "4"))
# Aborta queries que superen este tiempo (ms) para no colgar el pool.
STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "60000"))
CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT_S", "10"))


def init_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not DATABASE_URL:
                    raise RuntimeError("DATABASE_URL no configurada")
                _pool = ConnectionPool(
                    conninfo=DATABASE_URL,
                    min_size=POOL_MIN,
                    max_size=POOL_MAX,
                    timeout=30,                # espera máx. por una conexión libre
                    max_idle=300,
                    check=ConnectionPool.check_connection,  # descarta conexiones muertas
                    kwargs={
                        "row_factory": dict_row,
                        "connect_timeout": CONNECT_TIMEOUT_S,
                        "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
                    },
                )
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            log.exception("error cerrando pool")
        _pool = None


def ping() -> bool:
    """True si la DB responde. Nunca lanza (para healthchecks)."""
    try:
        with get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        log.exception("ping DB falló")
        return False


@contextmanager
def get_conn():
    pool = init_pool()
    with pool.connection() as c:
        yield c


def create_job(start_ts: datetime, end_ts: datetime) -> int:
    with get_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO asistencia.job (start_ts, end_ts, status) VALUES (%s,%s,'running') RETURNING id",
            (start_ts, end_ts),
        )
        return cur.fetchone()["id"]


def finish_job(job_id: int, *, status: str, eventos_raw: int = 0, eventos_ok: int = 0,
               resumen_filas: int = 0, error_msg: str | None = None, duracion: float = 0.0):
    if error_msg:
        error_msg = error_msg[:2000]  # no reventar por mensajes gigantes
    try:
        with get_conn() as c, c.cursor() as cur:
            cur.execute("""
                UPDATE asistencia.job
                SET status=%s, eventos_raw=%s, eventos_ok=%s, resumen_filas=%s,
                    error_msg=%s, duracion_seg=%s
                WHERE id=%s
            """, (status, eventos_raw, eventos_ok, resumen_filas, error_msg, duracion, job_id))
    except Exception:
        # finish_job se llama desde handlers de error: no debe tapar la excepción original
        log.exception(f"no pude cerrar job#{job_id}")


def upsert_eventos(job_id: int, eventos: list[dict]) -> int:
    """eventos: dicts con device, employee_no, name, event_time (datetime tz), major, minor, card_no, tipo"""
    if not eventos:
        return 0
    rows = [(
        e["device"], e["employee_no"], e.get("name") or None, e["event_time"],
        e.get("major") or None, e.get("minor") or None, e.get("card_no") or None,
        e.get("tipo"), job_id,
    ) for e in eventos]

    with get_conn() as c, c.cursor() as cur:
        cur.executemany("""
            INSERT INTO asistencia.evento
              (device, employee_no, employee_name, event_time, major, minor, card_no, tipo, job_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (employee_no, event_time, device) DO UPDATE
              SET employee_name = COALESCE(EXCLUDED.employee_name, asistencia.evento.employee_name),
                  tipo          = COALESCE(EXCLUDED.tipo, asistencia.evento.tipo),
                  job_id        = EXCLUDED.job_id
        """, rows)
    return len(rows)


def upsert_resumen(job_id: int, filas: list[dict]) -> int:
    if not filas:
        return 0
    rows = [(
        f["employee_no"], f.get("employee_name"), f["fecha"],
        f.get("check_in"), f.get("check_out"), f.get("minutos"),
        f.get("eventos_dia"), f.get("devices"), job_id,
    ) for f in filas]

    with get_conn() as c, c.cursor() as cur:
        cur.executemany("""
            INSERT INTO asistencia.resumen_diario
              (employee_no, employee_name, fecha, check_in, check_out, minutos, eventos_dia, devices, job_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (employee_no, fecha) DO UPDATE
              SET employee_name = EXCLUDED.employee_name,
                  check_in      = EXCLUDED.check_in,
                  check_out     = EXCLUDED.check_out,
                  minutos       = EXCLUDED.minutos,
                  eventos_dia   = EXCLUDED.eventos_dia,
                  devices       = EXCLUDED.devices,
                  job_id        = EXCLUDED.job_id
        """, rows)
    return len(rows)


def query_resumen(desde: date, hasta: date, employee_no: str | None = None) -> list[dict]:
    q = """
        SELECT employee_no, employee_name, fecha,
               (check_in  AT TIME ZONE 'America/Argentina/Buenos_Aires') AS check_in,
               (check_out AT TIME ZONE 'America/Argentina/Buenos_Aires') AS check_out,
               minutos, eventos_dia, devices
        FROM asistencia.resumen_diario
        WHERE fecha BETWEEN %s AND %s
    """
    params: list = [desde, hasta]
    if employee_no:
        q += " AND employee_no = %s"
        params.append(employee_no)
    q += " ORDER BY fecha, employee_no"
    with get_conn() as c, c.cursor() as cur:
        cur.execute(q, params)
        return cur.fetchall()


def query_eventos(desde: datetime, hasta: datetime, employee_no: str | None = None,
                  limit: int | None = None) -> list[dict]:
    q = """
        SELECT device, employee_no, employee_name,
               (event_time AT TIME ZONE 'America/Argentina/Buenos_Aires') AS event_time,
               tipo, major, minor
        FROM asistencia.evento
        WHERE event_time BETWEEN %s AND %s
    """
    params: list = [desde, hasta]
    if employee_no:
        q += " AND employee_no = %s"
        params.append(employee_no)
    q += " ORDER BY event_time"
    if limit is not None and limit > 0:
        q += " LIMIT %s"
        params.append(limit)
    with get_conn() as c, c.cursor() as cur:
        cur.execute(q, params)
        return cur.fetchall()
