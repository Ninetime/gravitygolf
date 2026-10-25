"""HSG6 gameapi wire protocol (reversed from NPUA80864 EBOOT, network_cmnRanking.cpp).

HTTP POST body:  p=<urlencode(BF_ECB(K_HTTP, body padded to 8 with 0x04))>
HTTP response:   BF_ECB(K_HTTP, tlv_stream) (client decrypts floor(len/8) blocks)
TLV element:     u16le tag, u16le size_in_words (incl. 4-byte header), data
Envelope:        0x8000 {} , 0x0000 {u32le result; 1=ok}, [0x0060 token], body..., 0x8010 {}
Token (0x0060):  BF_ECB(K_SESSION, "<anything>_<session>\0" padded) ; client keeps "_<session>"
"""
import os, struct, urllib.parse

K_HTTP = b"D9-Zs.2zsB@6"          # 0xd9e340, set on HTTP client (0x7f540c)
K_SESSION = b"l.2F5@B@w@E@26"     # 0xd9e350, ctx at cmnRanking+0x23e7a8

_T = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bf_tables.bin"), "rb").read()
_W = struct.unpack(">1042I", _T)
_P0, _S0 = _W[:18], _W[18:]


class Blowfish:
    def __init__(self, key: bytes):
        P = list(_P0)
        S = [list(_S0[i * 256:(i + 1) * 256]) for i in range(4)]
        j = 0
        for i in range(18):
            data = 0
            for _ in range(4):
                b = key[j % len(key)]
                if b >= 0x80:  # game uses signed char (extsb)
                    data = ((data << 8) | (b - 0x100)) & 0xffffffff
                else:
                    data = ((data << 8) | b) & 0xffffffff
                j += 1
            P[i] ^= data
        self.P, self.S = P, S
        l = r = 0
        for i in range(0, 18, 2):
            l, r = self._enc(l, r)
            P[i], P[i + 1] = l, r
        for s in S:
            for i in range(0, 256, 2):
                l, r = self._enc(l, r)
                s[i], s[i + 1] = l, r

    def _f(self, x):
        S = self.S
        return ((((S[0][x >> 24] + S[1][(x >> 16) & 0xff]) & 0xffffffff) ^ S[2][(x >> 8) & 0xff]) + S[3][x & 0xff]) & 0xffffffff

    def _enc(self, l, r):
        P = self.P
        for i in range(16):
            l ^= P[i]
            r ^= self._f(l)
            l, r = r, l
        l, r = r, l
        r ^= P[16]
        l ^= P[17]
        return l, r

    def _dec(self, l, r):
        P = self.P
        for i in range(17, 1, -1):
            l ^= P[i]
            r ^= self._f(l)
            l, r = r, l
        l, r = r, l
        r ^= P[1]
        l ^= P[0]
        return l, r

    def encrypt(self, data: bytes) -> bytes:
        out = bytearray()
        for i in range(0, len(data) - len(data) % 8, 8):
            l, r = struct.unpack(">II", data[i:i + 8])
            out += struct.pack(">II", *self._enc(l, r))
        return bytes(out)

    def decrypt(self, data: bytes) -> bytes:
        out = bytearray()
        for i in range(0, len(data) - len(data) % 8, 8):
            l, r = struct.unpack(">II", data[i:i + 8])
            out += struct.pack(">II", *self._dec(l, r))
        return bytes(out)


BF_HTTP = Blowfish(K_HTTP)
BF_SESSION = Blowfish(K_SESSION)


def pad8(b: bytes, fill: int = 4) -> bytes:
    n = (-len(b)) % 8
    return b + bytes([fill]) * n


# ---------------- TLV ----------------
def el(tag: int, data: bytes = b"") -> bytes:
    data = data + b"\0" * ((-len(data)) % 4)
    return struct.pack("<HH", tag, 1 + len(data) // 4) + data


def el_u32(tag, v):
    return el(tag, struct.pack("<I", v & 0xffffffff))


def el_i32(tag, v):
    return el(tag, struct.pack("<i", v))


def el_u16s(tag, *vals):
    return el(tag, struct.pack("<%dH" % len(vals), *vals))


def el_str(tag, s, minlen=0):
    b = s.encode("utf-8") if isinstance(s, str) else s
    b = b + b"\0"
    if len(b) < minlen:
        b += b"\0" * (minlen - len(b))
    return el(tag, b)


def bcd(n):
    return ((n // 10) << 4) | (n % 10)


def el_bcd_datetime(tag, dt):
    """7-byte BCD: CC YY MM DD hh mm ss (JST) -- see 0x526064."""
    y = dt.year
    b = bytes([bcd(y // 100), bcd(y % 100), bcd(dt.month), bcd(dt.day), bcd(dt.hour), bcd(dt.minute), bcd(dt.second)])
    return el(tag, b)


def parse(buf: bytes):
    """Return list of (tag, data) elements."""
    out, i = [], 0
    while i + 4 <= len(buf):
        tag, words = struct.unpack_from("<HH", buf, i)
        if words == 0:
            break
        out.append((tag, buf[i + 4:i + words * 4]))
        i += words * 4
    return out


def token(session: str) -> bytes:
    s = ("tk" + "_" + session).encode() + b"\0"
    return el(0x0060, BF_SESSION.encrypt(pad8(s, 0)))


def envelope(*body: bytes, result=1, session=None) -> bytes:
    parts = [el(0x8000), el_u32(0x0000, result)]
    if session is not None:
        parts.append(token(session))
    parts.extend(body)
    parts.append(el(0x8010))
    return b"".join(parts)


def encode_response(tlv: bytes) -> bytes:
    return BF_HTTP.encrypt(pad8(tlv, 0))


def decode_request(body: bytes) -> bytes:
    """body is the raw POST payload 'p=...'."""
    q = body
    if q.startswith(b"p="):
        q = q[2:]
    raw = urllib.parse.unquote_to_bytes(q)
    return BF_HTTP.decrypt(raw)


def encode_request(tlv: bytes) -> bytes:
    return b"p=" + urllib.parse.quote_from_bytes(BF_HTTP.encrypt(pad8(tlv, 4)), safe="").encode()


def dump(elems):
    lines = []
    for tag, data in elems:
        txt = data.rstrip(b"\0")
        show = txt.decode("latin1") if txt and all(32 <= c < 127 for c in txt) else data.hex()
        lines.append(f"  {tag:04x} [{len(data)}] {show}")
    return "\n".join(lines)


if __name__ == "__main__":
    bf = Blowfish(b"\0" * 8)
    assert bf.encrypt(b"\0" * 8).hex() == "4ef997456198dd78", bf.encrypt(b"\0" * 8).hex()
    bf = Blowfish(bytes.fromhex("1111111111111111"))  # key bytes < 0x80 (game sign-extends key bytes)
    assert bf.encrypt(bytes.fromhex("1111111111111111")).hex() == "2466dd878b963c9d"
    assert bf.decrypt(bytes.fromhex("2466dd878b963c9d")).hex() == "1111111111111111"
    t = envelope(el_u32(0x1234, 5), session="abc")
    assert BF_HTTP.decrypt(encode_response(t))[:len(t)] == t
    print("blowfish ok;", dump(parse(t)))
