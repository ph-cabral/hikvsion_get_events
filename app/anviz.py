import socket, struct

HOSTS = {"local": "10.10.0.147", "lilser": "10.10.0.241"}
PORT = 5010

def crc16(b):                      # Anviz = MCRF4XX
    crc = 0xFFFF
    for x in b:
        crc ^= x
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc

assert crc16(bytes.fromhex("a500000001bc00001200002700002b000002000000004e98000001")) == 0x7BD3

def frame(cmd, data=b"", dev=1):
    body = b"\xa5" + struct.pack(">I", dev) + bytes([cmd]) + struct.pack(">H", len(data)) + data
    c = crc16(body)
    return body + bytes([c & 0xFF, c >> 8 & 0xFF])

def io(ip, cmd, dev, t=2):
    s = socket.create_connection((ip, PORT), timeout=4)
    s.sendall(frame(cmd, b"", dev))
    s.settimeout(t)
    buf = b""
    try:
        while True:
            d = s.recv(4096)
            if not d: break
            buf += d
    except socket.timeout:
        pass
    s.close()
    return buf

def main():
    """Script de diagnóstico: prueba comandos contra los relojes. `python -m app.anviz`"""
    for name, ip in HOSTS.items():
        for dev in (1, 0, 0xFFFFFFFF):
            for cmd in (0x3C, 0x30):
                try:
                    r = io(ip, cmd, dev)
                    print(f"{name} dev={dev:#010x} cmd={cmd:#04x} -> {r.hex() or 'NADA'}")
                except Exception as e:
                    print(f"{name} dev={dev:#010x} cmd={cmd:#04x} ERR {e}")


if __name__ == "__main__":
    main()