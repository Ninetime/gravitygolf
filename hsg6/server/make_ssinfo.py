"""Build www/hsg6ps3/ss.info.

Format (reversed from EBOOT 0x52bdec / 0x7f9b94): base64 of a flat list of
<key>value</key> pairs. Values may themselves contain <tag>..</tag> pairs
(msg1 and gameurl1 are parsed a second time). All keys below are required.
"""
import base64, os
from config import BASE, MEDIUS_HOST, MEDIUS_PORT

GAMEAPI = ["login", "geteula", "getinfo", "getboardlist", "getscorelist", "regscore",
           "uploaddata", "getservertime", "getregulationdata", "getfinishboardlist",
           "getsignboardlist", "getsignboarddetail"]


def build():
    msg = "".join(f"<lang{i}>Welcome to the HSG6 revival server.</lang{i}>" for i in range(20))
    gameurl = "".join(f"<gameapi_{n}>{BASE}/gameapi/{n}</gameapi_{n}>" for n in GAMEAPI)
    pairs = [
        ("ss", "0"),                       # nonzero = maintenance (shows msg1)
        ("msg1", msg),
        ("daily_ver", "1030"),
        ("rank_ver", "1030"),
        ("lobby_ver", "1030"),
        ("scerturl1", MEDIUS_HOST),        # Medius MAS host
        ("scertport1", str(MEDIUS_PORT)),
        ("gameurl1", gameurl),
        ("realurl1", f"{BASE}/real/"),
        ("interval1", "60"),
        ("registscore_limithour1", "24"),
        ("finishboardlist_limitnum1", "30"),
    ]
    xml = "".join(f"<{k}>{v}</{k}>" for k, v in pairs)
    raw = xml.encode()
    # NB: no padding bytes outside tags -- the parser (0x7f9b94) fails on any
    # non-'<' printable char between elements. '=' base64 padding is handled.
    return base64.b64encode(raw)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www", "hsg6ps3", "ss.info")
    data = build()
    open(out, "wb").write(data)
    print(out, len(data))
    print(base64.b64decode(data).decode())
