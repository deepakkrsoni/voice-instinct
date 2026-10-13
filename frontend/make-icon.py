"""Write icon.png (64x64, violet dot) with the standard library only."""
import struct
import zlib

S = 64
rows = []
for y in range(S):
    row = bytearray([0])
    for x in range(S):
        d = ((x - S / 2 + 0.5) ** 2 + (y - S / 2 + 0.5) ** 2) ** 0.5
        row += bytes([139, 92, 246, 255]) if d < S / 2 - 2 else bytes([0, 0, 0, 0])
    rows.append(bytes(row))
raw = zlib.compress(b"".join(rows), 9)


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
open("icon.png", "wb").write(png)
