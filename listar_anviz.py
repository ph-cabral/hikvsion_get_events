#!/usr/bin/env python3
"""Lee el buffer del Anviz y lista registros de fechas dadas. NO inserta nada.
Uso: python3 listar_anviz.py [IP] [fecha ...]   (def: 10.10.0.147, 2026-06-06 2026-06-09)
"""
import socket, struct, sys
from datetime import datetime, timedelta
from collections import Counter

IP = sys.argv[1] if len(sys.argv) > 1 else "10.10.0.147"
FECHAS = set(sys.argv[2:]) or {"2026-06-06", "2026-06-09"}
PORT, EPOCH = 5010, datetime(2000, 1, 1)

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

def read_all(s, dev, modo_ini, batch=25):
    out, modo = [], modo_ini
    while True:
        r = parse(txn(s, 0x40, bytes([modo, batch]), dev=dev))
        if not r: break
        _, _, ret, pl = r
        if ret != 0 or not pl: break
        n = pl[0]
        for k in range(n):
            rec = pl[1 + k*14: 15 + k*14]
            if len(rec) == 14:
                out.append((int.from_bytes(rec[0:5], "big"),
                            EPOCH + timedelta(seconds=struct.unpack(">I", rec[5:9])[0])))
        if n < batch: break
        modo = 0x00
    return out

s = socket.create_connection((IP, PORT), timeout=5)
head = parse(txn(s, 0x30, dev=0))
if not head:
    sys.exit("sin respuesta del reloj (cmd 0x30)")
dev = struct.unpack(">I", head[0])[0]
recs = read_all(s, dev, 0x01)          # nuevos
if not recs:
    recs = read_all(s, dev, 0x02)      # todos (si ya fueron leídos)
s.close()

print(f"dev={hex(dev)} total buffer: {len(recs)}\n")
sel = [(u, t) for u, t in recs if t.strftime("%Y-%m-%d") in FECHAS]
for uid, ts in sorted(sel, key=lambda r: (r[1])):
    print(f"  anviz_id={uid:<6} {ts}")
print("\nfichadas por id:", dict(Counter(u for u, _ in sel)))
