"""'real' server (realurl1 + mg5_*.cgi), common SCEJ mg5 library in the EBOOT.

Request:  POST body 'param=' + urlencode(stream_encrypt(b'version0=%s,version1=%s'))
Response: stream_encrypt(buf) where buf = BE u32 checksum | b'I5gM' | payload
          checksum = (sum(buf with first 4 bytes zeroed) & 0xffffffff) ^ 0x3566476d   (0x916e88)
Cipher (0x9f412c): K = 32-byte table at 0xc2a568, i cycles 0..31, prev starts 0
          enc: c = ((p + prev) & 0xff) ^ K[i]; prev = p
          dec: p = ((c ^ K[i]) - prev) & 0xff;  prev = p
get_event_data payload (read natively / big-endian by 0x806e38.. accessors):
   +0x08 status (0 = OK; otherwise shown as T-%06X)
   +0x0c event entry count N (entries are 0x450 bytes at +0x20; +0x04 size, +0x50 file name)
   +0x10 base-URL count M (0x200-byte strings after the entries; one is picked at random,
         each entry is then fetched with GET '%s/%s' % (base, entry+0x50))
"""
import struct, urllib.parse
from config import BASE, DAY_OFFSET

K = bytes.fromhex("427664969c805d58579c198e054a260df5487fd40c663473886682b1b6373843")


def encrypt(p: bytes) -> bytes:
    out, prev = bytearray(), 0
    for i, b in enumerate(p):
        out.append(((b + prev) & 0xff) ^ K[i % 32])
        prev = b
    return bytes(out)


def decrypt(c: bytes) -> bytes:
    out, prev = bytearray(), 0
    for i, b in enumerate(c):
        p = ((b ^ K[i % 32]) - prev) & 0xff
        out.append(p)
        prev = p
    return bytes(out)


def wrap(payload_from_8: bytes) -> bytes:
    buf = bytearray(b"\0\0\0\0I5gM" + payload_from_8)
    s = (sum(buf) & 0xffffffff) ^ 0x3566476d
    buf[0:4] = struct.pack(">I", s)
    return encrypt(bytes(buf))


def decode_param(body: bytes) -> bytes:
    q = body[6:] if body.startswith(b"param=") else body
    return decrypt(urllib.parse.unquote_to_bytes(q))


def get_event_data(req: bytes) -> bytes:
    entries = []  # no event files yet
    bases = [f"{BASE}/real/files"]
    hdr = struct.pack(">IIIIII", 0, len(entries), len(bases), 0, 0, 0)  # +8..+0x1f
    body = hdr + b"".join(entries) + b"".join(b.encode().ljust(0x200, b"\0") for b in bases)
    return wrap(body)


def entry(size: int, name: str) -> bytes:
    """0x450-byte file entry: +0x04 size, +0x50 file name (fetched as base/name)."""
    e = bytearray(0x450)
    struct.pack_into(">I", e, 4, size)
    e[0x50:0x50 + len(name)] = name.encode()
    return bytes(e)


def get_app_data(req: bytes) -> bytes:
    """Same layout as event data; entry 0 is an FB archive of CSVs mounted at 0x77a080."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www", "hsg6ps3", "real", "files", "appdata.xb")
    size = os.path.getsize(path)
    bases = [f"{BASE}/real/files"]
    entries = [entry(size, "appdata.xb")]
    hdr = struct.pack(">IIIIII", 0, len(entries), len(bases), 0, 0, 0)
    body = hdr + b"".join(entries) + b"".join(b.encode().ljust(0x200, bytes(1)) for b in bases)
    return wrap(body)


def get_server_time(req: bytes) -> bytes:
    """caller 0x540558: +0x10 BE u64 = unix seconds (UTC); game adds 9h for JST."""
    import time
    return wrap(struct.pack(">IIQ", 0, 0, int(time.time()) + DAY_OFFSET * 86400) + bytes(0x10))


def ok_empty(req: bytes) -> bytes:
    """status 0, zero counts -- used for the subscriber-count calls while there are no events."""
    return wrap(bytes(0x40))


HANDLERS = {
    "mg5_get_event_data.cgi": get_event_data,
    "mg5_get_app_data.cgi": get_app_data,
    "mg5_get_server_time.cgi": get_server_time,
    "mg5_get_event_subscriber_count_list.cgi": ok_empty,
    "mg5_increment_event_subscriber_count.cgi": ok_empty,
    "mg5_decrement_event_subscriber_count.cgi": ok_empty,
}


def endpoint(name):
    def ep(http, query, body):
        try:
            req = decode_param(body)
        except Exception as e:  # noqa
            req = repr(e).encode()
        print(f"    [real/{name}] request: {req!r}", flush=True)
        fn = HANDLERS.get(name)
        if not fn:
            print(f"    [real/{name}] NOT IMPLEMENTED", flush=True)
            return 404, "text/plain", b"not found"
        return 200, "application/octet-stream", fn(req)
    return ep
