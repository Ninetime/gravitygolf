"""'FB' archive (.xb) packer/reader -- chlib MountBuffer format (0x778374, lookup 0x779228).

header (LE): 'FB\0\0', u32 count, u32 names_size (>=4, 4-aligned)
names:  names_size bytes (game ships none: 4 zero bytes)
entries: count x 5*u32 LE, sorted by hash:
         [hash, name_off, index, stored_size, raw_size(0 = not compressed)]
data:   files in index order, each padded to 4 bytes; zlib when raw_size != 0
hash:   h = h*31 + c over the lowercased path (java String.hashCode, unsigned)
"""
import struct, zlib


def fbhash(name: str) -> int:
    h = 0
    for c in name.lower().encode():
        h = (h * 31 + c) & 0xffffffff
    return h


def pack(files: dict, compress=False) -> bytes:
    # data blocks are stored in entry (hash-sorted) order; we also make index == position
    names = sorted(files, key=fbhash)
    blobs, ents = [], []
    for idx, n in enumerate(names):
        raw = files[n]
        if compress and raw:
            data, rawsz = zlib.compress(raw, 9), len(raw)
        else:
            data, rawsz = raw, 0
        blobs.append(data + b"\0" * ((-len(data)) % 4))
        ents.append((fbhash(n), 0, idx, len(data), rawsz))
    out =b"FB\0\0" + struct.pack("<II", len(names), 4) + b"\0" * 4
    out += b"".join(struct.pack("<5I", *e) for e in ents)
    return out + b"".join(blobs)


def unpack(d: bytes) -> dict:
    cnt, ns = struct.unpack_from("<II", d, 4)
    ents = [struct.unpack_from("<5I", d, 12 + (ns & ~3) + i * 20) for i in range(cnt)]
    off = 12 + (ns & ~3) + cnt * 20
    out = {}
    for e in ents:  # data follows entry order (verified on game archives)
        blob = d[off:off + e[3]]
        out[e[0]] = zlib.decompress(blob) if e[4] else blob
        off += (e[3] + 3) & ~3
    return out


if __name__ == "__main__":
    import sys
    a = pack({"data/menu/x.txt": b"hello", "y.csv": b"a,b\n"}, compress=True)
    r = unpack(a)
    assert r[fbhash("y.csv")] == b"a,b\n" and r[fbhash("data/menu/x.txt")] == b"hello"
    # round-trip check against a real archive
    if len(sys.argv) > 1:
        print({hex(k): len(v) for k, v in unpack(open(sys.argv[1], "rb").read()).items()})
    print("ok")
