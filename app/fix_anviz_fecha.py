"""
Corrige el desfase de fecha del reloj Anviz: durante un período grabó las
fichadas con la fecha atrasada un día entero. Se corre mes a mes, hacia
atrás desde el mes roto conocido, hasta el mes donde ya esté bien.

Flujo por mes:
  1. --diag   → muestra distribución por día de semana + primeras/últimas
                fichadas del mes, para confirmar a ojo si está corrido.
  2. (sin flags, default) → dry-run: cuenta cuántas filas tocaría, no escribe.
  3. --commit → aplica el corrimiento (con backup automático a
                asistencia.evento_fix_backup) y chequea colisiones antes
                de tocar nada.
  4. --revertir → restaura el mes desde el backup.

Uso:
  docker exec -it hikvision-api python -m app.fix_anviz_fecha --mes 2026-07 --diag
  docker exec -it hikvision-api python -m app.fix_anviz_fecha --mes 2026-07
  docker exec -it hikvision-api python -m app.fix_anviz_fecha --mes 2026-07 --commit
  docker exec -it hikvision-api python -m app.fix_anviz_fecha --mes 2026-07 --revertir

--dias: cuántos días sumar (default 1). Si el diagnóstico muestra que en
realidad está ADELANTADO un día (no atrasado), correr con --dias -1.
"""
import argparse
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db

log = logging.getLogger("fix_anviz_fecha")
BA = ZoneInfo("America/Argentina/Buenos_Aires")

BACKUP_DDL = """
CREATE TABLE IF NOT EXISTS asistencia.evento_fix_backup (
    id              BIGINT,
    device          VARCHAR(50),
    employee_no     VARCHAR(50),
    employee_name   VARCHAR(200),
    event_time_orig TIMESTAMPTZ,
    dias_aplicados  INT,
    fixed_at        TIMESTAMPTZ DEFAULT NOW()
);
"""


def _month_range(y: int, m: int):
    start = datetime(y, m, 1, tzinfo=BA)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = datetime(ny, nm, 1, tzinfo=BA)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def diag(cur, start, end):
    cur.execute(
        """
        SELECT to_char(event_time AT TIME ZONE 'America/Argentina/Buenos_Aires', 'Dy') AS dow,
               count(*) AS n
        FROM asistencia.evento
        WHERE device = 'anviz' AND event_time >= %s AND event_time < %s
        GROUP BY 1 ORDER BY 1
        """,
        (start, end),
    )
    print("-- distribución por día de semana --")
    for r in cur.fetchall():
        print(f"  {r['dow']}: {r['n']}")

    cur.execute(
        """
        SELECT employee_name,
               (event_time AT TIME ZONE 'America/Argentina/Buenos_Aires') AS t
        FROM asistencia.evento
        WHERE device = 'anviz' AND event_time >= %s AND event_time < %s
        ORDER BY event_time LIMIT 8
        """,
        (start, end),
    )
    print("-- primeras fichadas del mes --")
    for r in cur.fetchall():
        print(f"  {r['t']}  {r['employee_name']}")

    cur.execute(
        """
        SELECT employee_name,
               (event_time AT TIME ZONE 'America/Argentina/Buenos_Aires') AS t
        FROM asistencia.evento
        WHERE device = 'anviz' AND event_time >= %s AND event_time < %s
        ORDER BY event_time DESC LIMIT 8
        """,
        (start, end),
    )
    print("-- últimas fichadas del mes --")
    for r in cur.fetchall():
        print(f"  {r['t']}  {r['employee_name']}")


def contar(cur, start, end):
    cur.execute(
        """SELECT count(*) AS n FROM asistencia.evento
           WHERE device = 'anviz' AND event_time >= %s AND event_time < %s""",
        (start, end),
    )
    return cur.fetchone()["n"]


def colisiones(cur, start, end, dias):
    """Filas donde sumar `dias` pisaría un event_time que ya existe para el
    mismo empleado (violaría el UNIQUE employee_no, event_time, device)."""
    cur.execute(
        """
        SELECT count(*) AS n
        FROM asistencia.evento e1
        JOIN asistencia.evento e2
          ON e2.employee_no = e1.employee_no
         AND e2.device = 'anviz'
         AND e2.event_time = e1.event_time + (%s || ' days')::interval
        WHERE e1.device = 'anviz' AND e1.event_time >= %s AND e1.event_time < %s
        """,
        (dias, start, end),
    )
    return cur.fetchone()["n"]


def colisiones_detalle(cur, start, end, dias):
    cur.execute(
        """
        SELECT e1.id AS id_a_mover, e1.employee_no, e1.employee_name,
               (e1.event_time AT TIME ZONE 'America/Argentina/Buenos_Aires') AS orig,
               e2.id AS id_choca,
               (e2.event_time AT TIME ZONE 'America/Argentina/Buenos_Aires') AS ya_existe,
               e1.tipo AS tipo_a_mover, e2.tipo AS tipo_choca
        FROM asistencia.evento e1
        JOIN asistencia.evento e2
          ON e2.employee_no = e1.employee_no
         AND e2.device = 'anviz'
         AND e2.event_time = e1.event_time + (%s || ' days')::interval
        WHERE e1.device = 'anviz' AND e1.event_time >= %s AND e1.event_time < %s
        """,
        (dias, start, end),
    )
    return cur.fetchall()


def aplicar(cur, start, end, dias, excluir_colisiones=False):
    excl_sql = ""
    if excluir_colisiones:
        excl_sql = """
          AND NOT EXISTS (
              SELECT 1 FROM asistencia.evento e2
              WHERE e2.employee_no = asistencia.evento.employee_no
                AND e2.device = 'anviz'
                AND e2.event_time = asistencia.evento.event_time + (%(dias)s || ' days')::interval
          )
        """
    params = {"dias": dias, "start": start, "end": end}

    cur.execute(BACKUP_DDL)
    cur.execute(
        f"""
        INSERT INTO asistencia.evento_fix_backup
            (id, device, employee_no, employee_name, event_time_orig, dias_aplicados)
        SELECT id, device, employee_no, employee_name, event_time, %(dias)s
        FROM asistencia.evento
        WHERE device = 'anviz' AND event_time >= %(start)s AND event_time < %(end)s
        {excl_sql}
        """,
        params,
    )
    n_backup = cur.rowcount
    cur.execute(
        f"""
        UPDATE asistencia.evento
        SET event_time = event_time + (%(dias)s || ' days')::interval
        WHERE device = 'anviz' AND event_time >= %(start)s AND event_time < %(end)s
        {excl_sql}
        """,
        params,
    )
    return n_backup, cur.rowcount


def revertir(cur, start, end):
    cur.execute(
        """
        UPDATE asistencia.evento e
        SET event_time = b.event_time_orig
        FROM asistencia.evento_fix_backup b
        WHERE e.id = b.id AND e.device = 'anviz'
          AND b.event_time_orig >= %s AND b.event_time_orig < %s
        """,
        (start, end),
    )
    return cur.rowcount


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes", required=True, help="YYYY-MM")
    ap.add_argument("--dias", type=int, default=1,
                    help="días a sumar (default 1; usar -1 si está adelantado, no atrasado)")
    ap.add_argument("--diag", action="store_true", help="solo diagnóstico, no toca nada")
    ap.add_argument("--commit", action="store_true", help="aplica el corrimiento de verdad")
    ap.add_argument("--revertir", action="store_true", help="restaura el mes desde el backup")
    ap.add_argument("--ver-colisiones", action="store_true",
                    help="muestra el detalle de las filas que chocarían, no toca nada")
    ap.add_argument("--saltar-colisiones", action="store_true",
                    help="aplica el corrimiento igual, dejando afuera las filas que chocarían")
    args = ap.parse_args()

    y, m = map(int, args.mes.split("-"))
    start, end = _month_range(y, m)

    db.init_pool()
    with db.get_conn() as conn, conn.cursor() as cur:
        if args.diag:
            diag(cur, start, end)
            return

        if args.revertir:
            n = revertir(cur, start, end)
            conn.commit()
            print(f"revertidas {n} filas en {args.mes}")
            return

        if args.ver_colisiones:
            for r in colisiones_detalle(cur, start, end, args.dias):
                print(f"  id={r['id_a_mover']} {r['employee_name']} orig={r['orig']} "
                      f"({r['tipo_a_mover']})  ->  ya existe id={r['id_choca']} "
                      f"{r['ya_existe']} ({r['tipo_choca']})")
            return

        n = contar(cur, start, end)
        print(f"{args.mes}: {n} fichadas anviz en rango")
        if n == 0:
            print("nada para corregir.")
            return

        choques = colisiones(cur, start, end, args.dias)
        if choques and not args.saltar_colisiones:
            print(f"ABORTO: {choques} filas chocarían con un event_time ya existente "
                  f"(UNIQUE employee_no,event_time,device). "
                  f"Corré con --ver-colisiones para ver el detalle, o con --saltar-colisiones "
                  f"para aplicar el resto dejando esas afuera.")
            return
        if choques and args.saltar_colisiones:
            print(f"aviso: {choques} fila(s) quedan afuera por colisión (revisalas a mano después).")

        if not args.commit:
            print(f"dry-run: aplicaría +{args.dias} día(s) a {n} filas. "
                  f"Repetí con --commit para escribir (hace backup automático).")
            return

        n_backup, n_upd = aplicar(cur, start, end, args.dias,
                                   excluir_colisiones=args.saltar_colisiones)
        conn.commit()
        print(f"OK: backup {n_backup} filas, actualizadas {n_upd} filas en {args.mes} "
              f"({'+' if args.dias >= 0 else ''}{args.dias} día/s).")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
