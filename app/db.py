"""Capa Postgres con psycopg3 + pool."""
import logging
from contextlib import contextmanager
from datetime import datetime, date
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from .config import DATABASE_URL

log = logging.getLogger("db")

_pool: ConnectionPool | None = None


def init_pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=4, kwargs={"row_factory": dict_row})
    return _pool


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
    with get_conn() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE asistencia.job
            SET status=%s, eventos_raw=%s, eventos_ok=%s, resumen_filas=%s,
                error_msg=%s, duracion_seg=%s
            WHERE id=%s
        """, (status, eventos_raw, eventos_ok, resumen_filas, error_msg, duracion, job_id))


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


def query_eventos(desde: datetime, hasta: datetime, employee_no: str | None = None) -> list[dict]:
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
    with get_conn() as c, c.cursor() as cur:
        cur.execute(q, params)
        return cur.fetchall()