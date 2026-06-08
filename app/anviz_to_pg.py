#!/usr/bin/env python3
"""
Anviz 300 -> PostgreSQL
Lee las fichadas del reloj por TCP (puerto 5010) e inserta en PostgreSQL.
- UNA sola conexion al reloj (no lo cuelga), lee todo y cierra.
- Deduplica por (employee_no, fecha_hora): re-leer no duplica.
- Pensado para correr por cron cada N minutos.

Config por variables de entorno (o edita los defaults):
  ANVIZ_IP   = IP del reloj            (def 10.10.0.147)
  ANVIZ_PORT = puerto                  (def 5010)
  PG_DSN     = conexion PostgreSQL     (host port dbname user password)

Dependencia:  pip install psycopg2-binary
Cron ejemplo: */5 * * * * /usr/bin/python3 /ruta/anviz_to_pg.py >> /var/log/anviz.log 2>&1
"""
import os, socket, struct, sys
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import execute_values

ANVIZ_IP   = os.getenv("ANVIZ_IP", "10.10.0.147")
ANVIZ_PORT = int(os.getenv("ANVIZ_PORT", "5010"))
PG_DSN     = os.getenv("PG_DSN",
    "host=10.10.0.159 port=5432 dbname=proid142 user=n8n password=CAMBIAR")
TABLA      = os.getenv("PG_TABLE", "fichadas")
EPOCH      = datetime(2000, 1, 1)


def crc16(b):                       # Anviz = MCRF4XX
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


def txn(s, cmd, data=b"", dev=0, t=3):
    s.sendall(frame(cmd, data, dev)); s.settimeout(t); buf = b""
    try:
        while True:
            d = s.recv(8192)
            if not d: break
            buf += d
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
    return (
        int.from_bytes(rec[0:5], "big"),                              # employee_no
        EPOCH + timedelta(seconds=struct.unpack(">I", rec[5:9])[0]),  # fecha_hora
        rec[9],                                                       # verify
        rec[10],                                                      # estado
        int.from_bytes(rec[11:14], "big"),                           # workcode
        rec.hex(),                                                    # raw
    )


def read_records(s, dev, batch=25):
    out, modo = [], 0x01            # 0x01 = desde el inicio, 0x00 = siguiente lote
    while True:
        r = parse(txn(s, 0x40, bytes([modo, batch]), dev=dev))
        if not r: break
        _, _, ret, pl = r
        if ret != 0 or not pl: break
        n = pl[0]
        for k in range(n):
            rec = pl[1 + k*14: 15 + k*14]
            if len(rec) == 14:
                out.append(decode(rec))
        if n < batch: break
        modo = 0x00
    return out


def leer_reloj():
    s = socket.create_connection((ANVIZ_IP, ANVIZ_PORT), timeout=5)
    try:
        resp = parse(txn(s, 0x30, dev=0))
        if not resp:
            print("ERROR: el reloj no responde (puede estar colgado -> reinicialo)")
            sys.exit(1)
        dev = struct.unpack(">I", resp[0])[0]
        return dev, read_records(s, dev)
    finally:
        s.close()                   # cierre limpio: no deja la conexion tomada


def guardar_pg(recs):
    conn = psycopg2.connect(PG_DSN)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLA} (
                    employee_no integer   NOT NULL,
                    fecha_hora  timestamp NOT NULL,
                    verify      smallint,
                    estado      smallint,
                    workcode    integer,
                    raw         text,
                    PRIMARY KEY (employee_no, fecha_hora)
                );
            """)
            execute_values(cur,
                f"""INSERT INTO {TABLA}
                    (employee_no, fecha_hora, verify, estado, workcode, raw)
                    VALUES %s
                    ON CONFLICT (employee_no, fecha_hora) DO NOTHING""",
                recs)
            return cur.rowcount     # cuantas realmente nuevas
    finally:
        conn.close()


def main():
    dev, recs = leer_reloj()
    if not recs:
        print(f"0 fichadas leidas (dev {hex(dev)})"); return
    nuevas = guardar_pg(recs)
    print(f"{len(recs)} leidas, {nuevas} nuevas insertadas en {TABLA} (dev {hex(dev)})")


if __name__ == "__main__":
    main()
