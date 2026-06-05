import os, socket, struct
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import psycopg

IP     = os.getenv("ANVIZ_IP", "10.10.0.147")
DSN    = os.getenv("DATABASE_URL")          # postgres://postgres:PASS@n8n_sql:5432/n8n
DEVICE = "anviz"
EPOCH  = datetime(2000, 1, 1)
BA     = ZoneInfo("America/Argentina/Buenos_Aires")

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
    return dict(
        employee_no=int.from_bytes(rec[0:5], "big"),
        fecha_hora=EPOCH + timedelta(seconds=struct.unpack(">I", rec[5:9])[0]),
        verify=rec[9], estado=rec[10],
        workcode=int.from_bytes(rec[11:14], "big"),
    )

def read_records(s, dev, batch=25):
    out, modo = [], 0x01
    while True:
        r = parse(txn(s, 0x40, bytes([modo, batch]), dev=dev))
        if not r: break
        _, _, ret, pl = r
        if ret != 0 or not pl: break
        n = pl[0]
        for k in range(n):
            rec = pl[1 + k*14: 15 + k*14]
            if len(rec) == 14: out.append(decode(rec))
        if n < batch: break
        modo = 0x00
    return out

def main():
    with psycopg.connect(DSN) as cx, cx.cursor() as cur:
        cur.execute('SELECT "anvizId","employeeNo" FROM everwear.legajo WHERE "anvizId" IS NOT NULL')
        amap = {a: e for a, e in cur.fetchall()}

        s = socket.create_connection((IP, 5010), timeout=5)
        dev = struct.unpack(">I", parse(txn(s, 0x30, dev=0))[0])[0]
        recs = read_records(s, dev)
        s.close()

        rows, skip = [], 0
        for r in recs:
            emp = amap.get(str(r["employee_no"]))
            if emp is None:
                skip += 1; continue
            rows.append((DEVICE, emp, r["fecha_hora"].replace(tzinfo=BA)))

        cur.executemany(
            'INSERT INTO asistencia.evento (device, employee_no, event_time) '
            'VALUES (%s,%s,%s) ON CONFLICT (employee_no, event_time, device) DO NOTHING',
            rows,
        )
        cx.commit()
        print(f"leidas {len(recs)} | candidatas {len(rows)} | sin mapeo {skip} | dev {hex(dev)}")

if __name__ == "__main__":
    main()
