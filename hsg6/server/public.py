"""Public, read-only status page: server status, lobby usage, today's tournament, tour standings
and Daily tournament results.

Privacy rule: nothing here may tell who is online or when someone played. So the page shows
only counts per clubhouse / floor / lobby and room totals (no player or room names), and the
results show names, scores and points but no upload times.

Started by hsg6_http.py next to the admin page. Binds to 0.0.0.0:18082 by default
(HSG6_PUBLIC_BIND / HSG6_PUBLIC_PORT; HSG6_PUBLIC_PORT=0 disables it).

Not live on purpose: everything comes from one snapshot rebuilt at most every REFRESH seconds
(3 minutes), however many visitors there are. The snapshot carries an ETag, so a browser whose copy
is still current gets "304 Not Modified" instead of the data again.

  GET /                 public.html
  GET /theme.css, /img/<name>
  GET /api/public       snapshot: status, lobby usage, tour standings, Daily results (last
                        RESULT_DAYS days) and the schedule (next SCHEDULE_DAYS days)
"""
import datetime, hashlib, http.server, json, os, socketserver, threading, time, urllib.parse

import admin, gameapi, settings

ROOT = os.path.dirname(os.path.abspath(__file__))
BIND = os.environ.get("HSG6_PUBLIC_BIND", "0.0.0.0")
PORT = int(os.environ.get("HSG6_PUBLIC_PORT", "18082"))
REFRESH = 180          # seconds between snapshots
RESULT_DAYS = 14
SCHEDULE_DAYS = 14


def display_name(n):
    """The game logs in as '<name>#'; show it without the '#'."""
    return (n or "").removesuffix("#")


def _gamedata():
    return json.load(open(os.path.join(ROOT, "data", "gamedata.json"), encoding="utf-8"))


def _named(items, key, value):
    for it in items:
        if it.get(key) == value:
            return it.get("name", "")
    return ""


def describe(rs, gd):
    return {"label": rs.get("label", ""),
            "course": _named(gd["courses"], "id", int(rs.get("course", 0))),
            "holes": _named(gd["holes_modes"], "value", int(rs.get("holes", 0))),
            "tee": _named(gd["tees"], "value", int(rs.get("tee", 0)))}


def status():
    data, gd = settings.load(), _gamedata()
    today = admin.now_jst().date()
    med = admin.medius_status()
    ok = "error" not in med
    counts, room_count, room_players = {}, 0, 0
    if ok:
        for g in med["games"]:
            if g.get("appId") != admin.APP_ID:
                continue
            ln = settings.lobby_name(g["name"], data)
            if ln:
                k = (ln["club_i"], ln["floor_i"], ln["lobby_i"])
                counts[k] = counts.get(k, 0) + len(g["players"])
            else:
                room_count += 1
                room_players += len(g["players"])
    online = sum(1 for c in med.get("clients", []) if c.get("loggedIn") and c.get("appId") == admin.APP_ID)
    # every configured clubhouse / floor / lobby, including empty ones
    clubs = []
    for ci, c in enumerate(data["lobby"]["clubhouses"]):
        floors = []
        for fi, fname in enumerate(c["floors"]):
            lobbies = [{"name": l["name"], "map": l["map"], "capacity": l.get("capacity", 0),
                        "count": counts.get((ci, fi, li), 0)} for li, l in enumerate(c["lobbies"])]
            floors.append({"name": fname, "count": sum(l["count"] for l in lobbies), "lobbies": lobbies})
        clubs.append({"name": c["name"], "count": sum(f["count"] for f in floors), "floors": floors})
    sid, sname, sst, sen = settings.series_for(today, data)
    tp = gameapi.tour_points(today=today)
    standings = sorted(((display_name(p), v) for p, v in tp.items() if v > 0), key=lambda x: (-x[1], x[0]))
    return {
        "server_time_jst": admin.now_jst().isoformat(timespec="seconds"),
        "online_ok": ok,
        "players_online": online if ok else None,
        "lobby_players": sum(counts.values()),
        "rooms": room_count, "room_players": room_players,
        "clubhouses": clubs,
        "tournament_today": [dict(describe(rs, gd), slot=s, board=f"Daily_{today:%Y%m%d}_{s:02d}")
                             for s, _, rs in settings.tournaments_for(today, data)],
        "tour": {"name": sname, "start": sst.isoformat(), "end": sen.isoformat(),
                 "standings": _ranked(standings, key=lambda x: x[1], row=lambda x: {"name": x[0], "tp": x[1]})},
    }


def _ranked(items, key, row):
    """Standard competition ranking (1, 2, 2, 4)."""
    out, prev, rank = [], object(), 0
    for i, it in enumerate(items):
        if key(it) != prev:
            rank, prev = i + 1, key(it)
        out.append({"rank": rank, **row(it)})
    return out


def results(days):
    data, gd = settings.load(), _gamedata()
    db = admin.load_scores()
    today = admin.now_jst().date()
    first = today - datetime.timedelta(days=days - 1)
    out = []
    for board in sorted(db, reverse=True):
        try:
            d = datetime.datetime.strptime(board[6:14], "%Y%m%d").date()
            slot = int(board[15:17])
        except ValueError:
            continue
        if not first <= d <= today:
            continue
        rs = next((r for s, _, r in settings.tournaments_for(d, data) if s == slot), None)
        rows = sorted(db[board].items(), key=lambda x: (x[1].get("score", 0), -x[1].get("points", 0), x[0]))
        out.append({"board": board, "date": d.isoformat(), "in_progress": d == today,
                    **(describe(rs, gd) if rs else {"label": "", "course": "", "holes": "", "tee": ""}),
                    # no upload times on purpose (see module doc)
                    "results": _ranked(rows, key=lambda x: x[1].get("score", 0),
                                       row=lambda x: {"name": display_name(x[0]), "score": x[1].get("score", 0),
                                                      "points": x[1].get("points", 0)})})
    return out


def schedule(days):
    """Upcoming Daily tournaments, tomorrow onwards (today's is in the status part)."""
    data, gd = settings.load(), _gamedata()
    today = admin.now_jst().date()
    out = []
    for i in range(1, days + 1):
        d = today + datetime.timedelta(days=i)
        out.append({"date": d.isoformat(), "weekday": d.strftime("%a"),
                    "tournaments": [dict(describe(rs, gd), slot=s) for s, _, rs in settings.tournaments_for(d, data)]})
    return out


_cache = {"t": 0, "body": b"", "etag": ""}
_cache_lock = threading.Lock()


def snapshot():
    """(body, etag) of the current snapshot; rebuilt when older than REFRESH seconds."""
    with _cache_lock:
        if time.time() - _cache["t"] >= REFRESH or not _cache["body"]:
            snap = status()
            snap["results"] = results(RESULT_DAYS)
            snap["schedule"] = schedule(SCHEDULE_DAYS)
            # the ETag covers the content only, so an unchanged rebuild keeps the same tag
            content = json.dumps({k: v for k, v in snap.items() if k != "server_time_jst"},
                                 ensure_ascii=False, sort_keys=True).encode("utf-8")
            etag = '"' + hashlib.sha1(content).hexdigest()[:20] + '"'
            if etag != _cache["etag"]:
                snap["updated"] = snap["server_time_jst"]
                _cache["body"] = json.dumps(snap, ensure_ascii=False).encode("utf-8")
                _cache["etag"] = etag
            _cache["t"] = time.time()
        age = int(time.time() - _cache["t"])
        return _cache["body"], _cache["etag"], max(5, REFRESH - age)


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, ctype="application/json", raw=None, headers=None):
        data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, ctype="text/html; charset=utf-8",
                                  raw=open(os.path.join(ROOT, "admin", "public.html"), "rb").read())
            if u.path == "/theme.css":
                return self._send(200, ctype="text/css; charset=utf-8",
                                  raw=open(os.path.join(ROOT, "admin", "theme.css"), "rb").read())
            if u.path.startswith("/img/"):
                name = os.path.basename(u.path)
                path = os.path.join(ROOT, "admin", "img", name)
                if not os.path.isfile(path):
                    return self._send(404, {"error": "no such image"})
                return self._send(200, ctype="image/png" if name.endswith(".png") else "image/jpeg",
                                  raw=open(path, "rb").read())
            if u.path == "/api/public":
                body, etag, next_in = snapshot()
                hdr = {"ETag": etag, "X-Next-Refresh": str(next_in)}
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    for k, v in hdr.items():
                        self.send_header(k, v)
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    return
                return self._send(200, ctype="application/json; charset=utf-8", raw=body, headers=hdr)
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start():
    if not PORT:
        return None
    srv = S((BIND, PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="public").start()
    print(f"HSG6 public status page on http://{BIND}:{PORT}/", flush=True)
    return srv


if __name__ == "__main__":
    start()
    threading.Event().wait()
