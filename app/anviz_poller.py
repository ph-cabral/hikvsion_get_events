"""
Conexión con el reloj Anviz (protocolo TCP, puerto 5010) e inserción de
fichajes en asistencia.evento, integrado con el pool y el job-tracking de la app.

El protocolo binario (crc16/frame/txn/parse/decode/read_records) está verificado
contra el equipo: NO modificar esas funciones.
"""
import socket
import struct
import time
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import ANVIZ_IP, ANVIZ_PORT, ANVIZ_DEVICE, CLASIF_CUTOFF_HOUR

log = logging.getLogger("anviz")

# El contador de segundos del reloj esta 1 dia atrasado respecto del epoch
# estandar Anviz (2000-01-01). Confirmado 2026-08-26 con diag_anviz_epoch:
# crudo=840959840 -> 2026-08-25 07:57 con epoch 2000-01-01, cuando la fichada
# real es del miercoles 2026-08-26 07:57. Compensa 2000-01-02.
EPOCH = datetime(2000, 1, 2)
BA = ZoneInfo("America/Argentina/Buenos_Aires")

# ---- protocolo Anviz (verificado, sin cambios) ----
def crc16(b):
    crc = 0xFFFF
    for x in b:
        crc ^= x
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc

def frame(cmd, data=b"", dev=0):
    body = b"\xa5" + struct.pack(">I", dev) + bytes([cmd]) + struct.pack(">H", len(data)) + data
    c = crc16(body)
    return body + bytes([c & 0xFF, c >> 8 & 0xFF])

def _frame_listo(buf: bytes) -> bool:
    """True si `buf` ya contiene una respuesta completa de las que devuelve
    `parse` (salteando los ACK 0xDF de "ocupado", igual que parse).

    Existe para que `txn` corte en cuanto llegó la respuesta en vez de esperar
    SIEMPRE a que venza el timeout del socket. Con lotes de 25 registros y
    t=3s, bajar ~800 fichadas costaba ~100 s de puro sleep y la lectura nunca
    alcanzaba el final del buffer (diagnosticado 2026-08-25: `ultima` clavada
    en abril mientras el reloj seguia grabando bien).
    """
    i = 0
    while i < len(buf):
        if buf[i] != 0xA5:
            i += 1
            continue
        if len(buf) < i + 9:
            return False
        ack = buf[i + 5]
        ln = struct.unpack(">H", buf[i + 7:i + 9])[0]
        fin = i + 9 + ln + 2
        if len(buf) < fin:
            return False
        if ack == 0xDF:
            i = fin
            continue
        return True
    return False


def txn(s, cmd, data=b"", dev=0, t=3):
    s.sendall(frame(cmd, data, dev)); s.settimeout(t); buf = b""
    try:
        while True:
            d = s.recv(8192)
            if not d: break
            buf += d
            if _frame_listo(buf): break
    except socket.timeout:
        pass
    return buf

def parse(buf):
    i = 0
    while i < len(buf):
        if buf[i] != 0xA5: i += 1; continue
        ack = buf[i+5]; ret = buf[i+6]
        ln = struct.unpack(">H", buf[i+7:i+9])[0]
        ch = buf[i+1:i+5]; pl = buf[i+9:i+9+ln]; i += 9 + ln + 2
        if ack == 0xDF: continue
        return ch, ack, ret, pl
    return None

def decode(rec):
    return dict(
        employee_no=int.from_bytes(rec[0:5], "big"),
        fecha_hora=EPOCH + timedelta(seconds=struct.unpack(">I", rec[5:9])[0]),
        verify=rec[9], estado=rec[10],
        workcode=int.from_bytes(rec[11:14], "big"),
    )

class LecturaIncompleta(RuntimeError):
    """La descarga del buffer se corto a mitad. `parciales` es un PREFIJO."""

    def __init__(self, parciales):
        super().__init__(f"lectura truncada en {len(parciales)} registros")
        self.parciales = parciales


def read_records(s, dev, batch=25, *, reintentos_lote=4):
    """Descarga el buffer entero. Devuelve la lista o levanta LecturaIncompleta.

    Diferencia con la version original: un lote que NO contesta ya no corta la
    descarga haciendola pasar por "fin del buffer". Se reintenta el lote (modo
    0x00 = continuar) y, si insiste, se levanta LecturaIncompleta para que el
    llamador sepa que lo que tiene es un prefijo. El "fin real" sigue siendo el
    mismo de antes: un lote con menos de `batch` registros, o ret!=0/payload
    vacio (el reloj avisando que no hay mas).
    """
    out, modo = [], 0x01
    fallos = 0
    while True:
        r = parse(txn(s, 0x40, bytes([modo, batch]), dev=dev))
        if not r:
            # timeout / basura: NO es fin de buffer, es un corte.
            fallos += 1
            if fallos > reintentos_lote:
                raise LecturaIncompleta(out)
            modo = 0x00
            time.sleep(0.4 * fallos)
            continue
        _, _, ret, pl = r
        if ret != 0 or not pl:
            return out
        fallos = 0
        n = pl[0]
        for k in range(n):
            rec = pl[1 + k*14: 15 + k*14]
            if len(rec) == 14: out.append(decode(rec))
        if n < batch:
            return out
        modo = 0x00

# ---- integración con la app ----
def _rec_key(r: dict) -> tuple:
    """Clave única de una fichada decodificada, para mergear lecturas parciales."""
    return (r["employee_no"], r["fecha_hora"], r["verify"], r["estado"], r["workcode"])


def fetch_records(ip: str | None = None, port: int | None = None,
                  timeout: int = 5, retries: int = 2,
                  *, lecturas_max: int = 5, lecturas_estables: int = 2):
    """Conecta al reloj, devuelve (dev_id, [registros decodificados]).

    El reloj es flaky: bajo conexión inestable, `read_records` puede cortarse
    a mitad de la descarga del buffer y devolver eso como si fuera todo (no
    distingue "fin real del buffer" de "se cortó a mitad de un lote" — ver
    2026-08-13, se confirmó con varias lecturas que la fecha "primera"
    siempre daba igual pero "ultima"/el total variaban muchísimo entre
    llamadas). Como la lectura siempre arranca desde el registro más viejo,
    una lectura corta es un PREFIJO de la real y lo más nuevo (al final) es
    lo que más se pierde.

    Por eso acá hacemos varias conexiones/lecturas y las vamos mergeando por
    clave única, hasta que unas cuantas lecturas seguidas no aporten nada
    nuevo (`lecturas_estables`) o se llegue a `lecturas_max` intentos. Si
    ninguna conexión prospera (reloj inaccesible), reintenta `retries` veces
    antes de darse por vencido, igual que antes.
    """
    ip = ip or ANVIZ_IP
    port = port or ANVIZ_PORT
    dev_id: int | None = None
    merged: dict[tuple, dict] = {}
    estables = 0
    alguna_completa = False
    fallos_seguidos = 0
    last: Exception | None = None

    for intento in range(max(1, lecturas_max)):
        try:
            s = socket.create_connection((ip, port), timeout=timeout)
            try:
                head = parse(txn(s, 0x30, dev=0))
                if not head:
                    raise RuntimeError("sin respuesta del reloj (cmd 0x30)")
                dev = struct.unpack(">I", head[0])[0]
                dev_id = dev
                try:
                    recs = read_records(s, dev)
                    completa = True
                except LecturaIncompleta as inc:
                    recs = inc.parciales
                    completa = False
            finally:
                try:
                    s.close()
                except Exception:
                    pass
        except (OSError, RuntimeError) as e:
            last = e
            fallos_seguidos += 1
            log.warning(f"anviz lectura {intento + 1}/{lecturas_max} falló "
                        f"({fallos_seguidos}/{retries} fallos seguidos): {e}")
            if fallos_seguidos >= retries and dev_id is None:
                # nunca conectó ni una vez: no hay datos parciales que devolver
                raise RuntimeError(
                    f"anviz inaccesible tras {retries} intentos: {last}"
                ) from last
            time.sleep(1.0 * fallos_seguidos)
            continue

        fallos_seguidos = 0
        nuevos = 0
        for r in recs:
            k = _rec_key(r)
            if k not in merged:
                merged[k] = r
                nuevos += 1
        log.info(f"anviz lectura {intento + 1}/{lecturas_max}: {len(recs)} leidos "
                 f"({'COMPLETA' if completa else 'TRUNCADA'}), {nuevos} nuevos "
                 f"(acumulado {len(merged)})")

        if completa:
            # Una lectura que llego al final del buffer es autoritativa:
            # no tiene sentido seguir insistiendo.
            alguna_completa = True
            break

        # OJO: una lectura TRUNCADA no cuenta como "estable". Dos cortes en el
        # mismo punto devuelven el mismo prefijo y hacian creer que ya estaba
        # todo descargado (era el bug que dejaba las fichadas nuevas afuera).
        estables = 0

    if dev_id is None:
        raise RuntimeError(f"anviz inaccesible tras {retries} intentos: {last}") from last

    if not alguna_completa:
        log.warning(f"anviz: ninguna lectura llego al final del buffer en "
                    f"{lecturas_max} intentos; devuelvo {len(merged)} registros "
                    f"mergeados (pueden faltar las fichadas mas nuevas)")

    return dev_id, list(merged.values())


def _emp_map(conn) -> dict[str, tuple]:
    """anvizId(str) -> (employeeNo, nombre) desde everwear.legajo."""
    with conn.cursor() as cur:
        cur.execute(
            '''SELECT "anvizId" AS aid, "employeeNo" AS emp, NULLIF(TRIM(nombre), '') AS nombre
               FROM everwear.legajo WHERE "anvizId" IS NOT NULL'''
        )
        out: dict[str, tuple] = {}
        for row in cur.fetchall():
            aid = row["aid"]
            if aid is None:
                continue
            out[str(aid).strip()] = (row["emp"], row["nombre"])
        return out


def poll(start: datetime | None = None, end: datetime | None = None,
         *, ip: str | None = None, port: int | None = None) -> dict:
    """
    Descarga los fichajes del Anviz y los upserta en asistencia.evento.
    start/end: datetimes tz-aware opcionales para filtrar el rango (en UTC interno).
    Idempotente (ON CONFLICT employee_no,event_time,device).
    """
    t0 = time.time()
    now = datetime.now(timezone.utc)
    s_utc = start.astimezone(timezone.utc) if start else now
    e_utc = end.astimezone(timezone.utc) if end else now
    job_id = db.create_job(s_utc, e_utc)
    try:
        with db.get_conn() as conn:
            amap = _emp_map(conn)

        dev, recs = fetch_records(ip, port)

        eventos, skip = [], 0
        for r in recs:
            t_ar = r["fecha_hora"].replace(tzinfo=BA)
            t_utc = t_ar.astimezone(timezone.utc)
            if start and t_utc < s_utc:
                continue
            if end and t_utc > e_utc:
                continue
            mapped = amap.get(str(r["employee_no"]))
            if not mapped or not mapped[0]:
                skip += 1
                continue
            emp_no, nombre = mapped
            tipo = "ENTRADA" if t_ar.hour < CLASIF_CUTOFF_HOUR else "SALIDA"
            eventos.append({
                "device": ANVIZ_DEVICE,
                "employee_no": str(emp_no),
                "name": nombre,
                "event_time": t_utc,
                "major": None, "minor": None, "card_no": None,
                "tipo": tipo,
            })

        n_ok = db.upsert_eventos(job_id, eventos)
        dur = time.time() - t0
        db.finish_job(job_id, status="ok", eventos_raw=len(recs), eventos_ok=n_ok, duracion=dur)
        log.info(f"anviz dev={hex(dev)} leidas={len(recs)} ok={n_ok} sin_mapeo={skip}")
        return {
            "job_id": job_id, "status": "ok", "device": ANVIZ_DEVICE, "dev_id": hex(dev),
            "leidas": len(recs), "eventos_ok": n_ok, "sin_mapeo": skip,
            "duracion_seg": round(dur, 2),
        }
    except Exception as ex:
        dur = time.time() - t0
        log.exception("anviz poll falló")
        db.finish_job(job_id, status="error", error_msg=str(ex), duracion=dur)
        return {"job_id": job_id, "status": "error", "error": str(ex), "duracion_seg": round(dur, 2)}


def debug_raw() -> dict:
    """Lee el buffer del reloj SIN filtrar ni guardar en DB. Agrupa por el ID
    interno del reloj (anviz_id) para ver qué usuarios tiene y cuáles ya están
    vinculados a un legajo (everwear.legajo.anvizId)."""
    with db.get_conn() as conn:
        amap = _emp_map(conn)

    dev, recs = fetch_records()

    por_id: dict[str, dict] = {}
    for r in recs:
        aid = str(r["employee_no"])
        t_ar = r["fecha_hora"].replace(tzinfo=BA)
        e = por_id.setdefault(aid, {"cantidad": 0, "primera": t_ar, "ultima": t_ar})
        e["cantidad"] += 1
        if t_ar < e["primera"]:
            e["primera"] = t_ar
        if t_ar > e["ultima"]:
            e["ultima"] = t_ar

    usuarios = []
    for aid, e in sorted(por_id.items(), key=lambda kv: -kv[1]["cantidad"]):
        mapped = amap.get(aid)
        usuarios.append({
            "anviz_id": aid,
            "cantidad_fichadas": e["cantidad"],
            "primera": e["primera"].isoformat(),
            "ultima": e["ultima"].isoformat(),
            "vinculado": mapped is not None,
            "employee_no": mapped[0] if mapped else None,
            "nombre": mapped[1] if mapped else None,
        })

    return {
        "device": ANVIZ_DEVICE, "dev_id": hex(dev), "total_fichadas": len(recs),
        "usuarios_distintos": len(usuarios),
        "vinculados": sum(1 for u in usuarios if u["vinculado"]),
        "sin_vincular": sum(1 for u in usuarios if not u["vinculado"]),
        "usuarios": usuarios,
    }


def main():
    """CLI: descarga todo el buffer del reloj y lo guarda. `python -m app.anviz_poller`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    db.init_pool()
    print(poll())


if __name__ == "__main__":
    main()
