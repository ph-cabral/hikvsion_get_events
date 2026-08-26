"""Diagnóstico del desfase de 1 día del Anviz. SOLO LECTURA: no toca la DB.

Lee el buffer del reloj y muestra, para las fichadas más nuevas, los SEGUNDOS
CRUDOS del registro y qué fecha da cada epoch candidato. Sirve para decidir si
el corrimiento está en `decode()` (EPOCH mal por 86400 s) o en el reloj mismo.

    docker exec -it hikvision-api python -m app.diag_anviz_epoch
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .anviz_poller import EPOCH, fetch_records

BA = ZoneInfo("America/Argentina/Buenos_Aires")
CANDIDATOS = {
    "2000-01-01 (estandar) ": datetime(2000, 1, 1),
    "2000-01-02 (el actual)": datetime(2000, 1, 2),
    "1999-12-31 (-1 dia)   ": datetime(1999, 12, 31),
}


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    dev, recs = fetch_records()
    ahora = datetime.now(BA)
    print(f"\ndev={hex(dev)}  registros={len(recs)}")
    print(f"ahora (AR) = {ahora:%Y-%m-%d %H:%M}  ({ahora:%A})\n")

    # `decode` ya sumo el EPOCH vigente, asi que reconstruyo los segundos crudos
    # restando ESE epoch (no uno fijo, o el diag miente cuando el epoch cambia).
    base = EPOCH
    for r in sorted(recs, key=lambda x: x["fecha_hora"])[-8:]:
        seg = int((r["fecha_hora"] - base).total_seconds())
        print(f"anviz_id={r['employee_no']:>4}  crudo={seg}")
        for nombre, ep in CANDIDATOS.items():
            t = ep + timedelta(seconds=seg)
            print(f"    {nombre} -> {t:%Y-%m-%d %H:%M:%S}  ({t:%A})")
        print()

    print("Leer asi: la fichada mas nueva de alguien que vino HOY tiene que caer "
          "en la fecha y el dia de semana de hoy.\nEl epoch que la acierte es el "
          "correcto.")


if __name__ == "__main__":
    main()
