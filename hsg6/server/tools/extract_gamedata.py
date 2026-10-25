"""Extract course / rule names and rule flags from the installed game into data/gamedata.json.

Usage: python tools/extract_gamedata.py <USRDIR>
  USRDIR = .../dev_hdd0/game/NPUA80864/USRDIR
Sources: xbdata/menu/message_text.US_01.xb (FB archive of TO1 text tables)
         appdata/Rule_Rule.txt (rule table; column 'Official')
"""
import json, os, struct, sys, zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fb_blobs(path):
    d = open(path, "rb").read()
    cnt, ns = struct.unpack_from("<II", d, 4)
    base = 12 + (ns & ~3)
    ents = [struct.unpack_from("<5I", d, base + i * 20) for i in range(cnt)]
    off, out = base + cnt * 20, []
    for e in ents:
        b = d[off:off + e[3]]
        off += (e[3] + 3) & ~3
        out.append(zlib.decompress(b) if e[4] else b)
    return out


def to_tables(b):
    """TO1 text file: 'TO1\0', u32 hash, u32 n, n x (u32 maxlen, u32 off, u32 count) -> string offsets."""
    n = struct.unpack_from("<I", b, 8)[0]
    out = []
    for t in range(n):
        _, o, c = struct.unpack_from("<III", b, 12 + t * 12)
        strs = []
        for k in range(c):
            so = struct.unpack_from("<I", b, o + 4 * k)[0]
            strs.append(b[so:b.index(b"\0", so)].decode("utf-8", "replace"))
        out.append(strs)
    return out


def find_table(blobs, needle):
    for b in blobs:
        if b[:3] != b"TO1":
            continue
        try:
            for t in to_tables(b):
                if needle in t:
                    return t
        except Exception:
            pass
    raise SystemExit(f"table containing {needle!r} not found")


def main(usrdir):
    blobs = fb_blobs(os.path.join(usrdir, "xbdata", "menu", "message_text.US_01.xb"))
    courses = find_table(blobs, "Maple Leaf Golf Club")
    rules = find_table(blobs, "Real Golf Mode")
    official = {}
    rr = open(os.path.join(HERE, "appdata", "Rule_Rule.txt"), encoding="utf-8-sig").read().splitlines()
    hdr = rr[0].split("\t")
    for line in rr[1:]:
        f = line.split("\t")
        if len(f) > 2 and f[1].isdigit():
            official[int(f[1])] = f[hdr.index("Official")] == "1"

    def opts(base, n):
        return [{"value": v, "name": rules[base + v]} for v in range(n)
                if base + v < len(rules) and rules[base + v].strip() not in ("", "???")]

    data = {
        "courses": [{"id": i, "name": n} for i, n in enumerate(courses) if 1 <= i <= 20 and n not in ("???", " ")],
        # regulation +0x55 -> round setup 0x4e0578: (holes, section, order); section 1 = front,
        # 2 = back, 4 = random; order 2 = shuffled with the regulation seed (+0x4e)
        "holes_modes": [{"value": 0, "name": "18 holes"},
                        {"value": 1, "name": "9 holes (random holes, shuffled)"},
                        {"value": 2, "name": "9 holes (OUT / front nine)"},
                        {"value": 3, "name": "9 holes (IN / back nine)"},
                        {"value": 4, "name": "6 holes (random holes, shuffled)"},
                        {"value": 5, "name": "3 holes (random holes, shuffled)"}],
        "tees": [{"value": 0, "name": "Regular tee"}, {"value": 1, "name": "Long tee (mirror)"}],
        "rules": [{"id": i, "name": n, "unofficial": official.get(i, False)}
                  for i, n in enumerate(rules) if n.strip() not in ("", "???")],
        "clubs": opts(0x17, 0x1d),
        "balls": opts(0x34, 0x23),
        "characters": opts(0x57, 0x2e),
    }
    out = os.path.join(HERE, "data", "gamedata.json")
    json.dump(data, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(out, {k: len(v) for k, v in data.items()})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"D:\Games\rpcs3\dev_hdd0\game\NPUA80864\USRDIR")
