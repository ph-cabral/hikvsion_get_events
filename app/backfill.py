"""
Backfill mes a mes hacia atrás, hasta que un mes devuelva 0 eventos
(o se alcance MAX_MESES de seguridad).

Uso:
  docker exec -it hikvision-api python -m app.backfill
  docker exec -it hikvision-api python -m app.backfill --desde 2022-01
  docker exec -it hikvision-api python -m app.backfill --max-vacios 3

Para correr en background sin bloquear:
  docker exec -d hikvision-api python -m app.backfill
  docker logs -f hikvision-api
"""
import argparse
import logging
import sys
import time
from datetime import datetime, date

from . import db, jobs
from .jobs import TZ_AR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s backfill | %(message)s",
)
log = logging.getLogger("backfill")


def _first_day(y: int, m: int) -> datetime:
    return datetime(y, m, 1, 0, 0, 0, tzinfo=TZ_AR)


def _last_day_next_month(y: int, m: int) -> datetime:
    # primer día del mes siguiente, menos 1 segundo
    if m == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m + 1
    return datetime(ny, nm, 1, 0, 0, 0, tzinfo=TZ_AR).replace() \
        .fromtimestamp(datetime(ny, nm, 1, 0, 0, 0, tzinfo=TZ_AR).timestamp() - 1, tz=TZ_AR)


def _month_range(y: int, m: int) -> tuple[datetime, datetime]:
    start = _first_day(y, m)
    # fin = primer día mes siguiente - 1 segundo
    if m == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m + 1
    end_excl = datetime(ny, nm, 1, 0, 0, 0, tzinfo=TZ_AR)
    end = datetime.fromtimestamp(end_excl.timestamp() - 1, tz=TZ_AR)
    return start, end


def _prev_month(y: int, m: int) -> tuple[int, int]:
    if m == 1:
        return y - 1, 12
    return y, m - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="YYYY-MM mes inicial (default: mes actual AR)")
    ap.add_argument("--hasta", help="YYYY-MM mes final más antiguo (corta acá si llega)")
    ap.add_argument("--max-vacios", type=int, default=2,
                    help="cortar después de N meses consecutivos con 0 eventos (default 2)")
    ap.add_argument("--max-meses", type=int, default=120,
                    help="tope duro de seguridad en meses (default 120 = 10 años)")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="pausa en segundos entre meses (default 1.0)")
    args = ap.parse_args()

    # mes inicial
    if args.desde:
        y, m = map(int, args.desde.split("-"))
    else:
        now = datetime.now(TZ_AR)
        y, m = now.year, now.month

    # mes mínimo (más antiguo permitido)
    min_y = min_m = None
    if args.hasta:
        min_y, min_m = map(int, args.hasta.split("-"))

    db.init_pool()
    log.info(f"arranco backfill desde {y:04d}-{m:02d} hacia atrás "
             f"(max_vacios={args.max_vacios}, max_meses={args.max_meses})")

    vacios_seguidos = 0
    meses_procesados = 0
    totales = {"eventos_ok": 0, "resumen_filas": 0, "meses": 0}

    while meses_procesados < args.max_meses:
        start, end = _month_range(y, m)
        log.info(f"==> {y:04d}-{m:02d}  [{start.date()} → {end.date()}]")
        t0 = time.time()
        try:
            res = jobs.run_sync(start, end)
        except Exception as e:
            log.exception(f"mes {y:04d}-{m:02d} falló: {e}")
            res = {"eventos_ok": 0, "resumen_filas": 0, "eventos_raw": 0}

        dur = time.time() - t0
        log.info(
            f"<== {y:04d}-{m:02d}  raw={res.get('eventos_raw', 0)}  "
            f"ok={res.get('eventos_ok', 0)}  resumen={res.get('resumen_filas', 0)}  "
            f"{dur:.1f}s"
        )
        totales["eventos_ok"] += res.get("eventos_ok", 0)
        totales["resumen_filas"] += res.get("resumen_filas", 0)
        totales["meses"] += 1
        meses_procesados += 1

        # corte por vacíos consecutivos
        if res.get("eventos_ok", 0) == 0 and res.get("eventos_raw", 0) == 0:
            vacios_seguidos += 1
            if vacios_seguidos >= args.max_vacios:
                log.info(f"{vacios_seguidos} meses vacíos seguidos → corto.")
                break
        else:
            vacios_seguidos = 0

        # corte por --hasta
        if min_y is not None and (y, m) <= (min_y, min_m):
            log.info(f"alcanzado --hasta {min_y:04d}-{min_m:02d} → corto.")
            break

        y, m = _prev_month(y, m)
        time.sleep(args.sleep)

    log.info(f"FIN. meses={totales['meses']}  "
             f"eventos_ok={totales['eventos_ok']}  "
             f"resumen_filas={totales['resumen_filas']}")


if __name__ == "__main__":
    sys.exit(main())
