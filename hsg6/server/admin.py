"""Admin web page + JSON API (status, tournaments, series, rule sets, lobby layout, news, scores).

Started by hsg6_http.py in a background thread. Binds to every address of the machine (0.0.0.0:18080) by default;
set HSG6_ADMIN_BIND=127.0.0.1 to allow only local access (HSG6_ADMIN_PORT changes the port). Every page and API call needs a login (auth.py): the
first visit from the server machine creates the admin account; reset it with
    python admin.py set-password <user>
State-changing calls must also carry the header "X-HSG6: 1" (blocks cross-site form posts).
The public, read-only status page is a separate server (public.py).

Login API (no session needed):
  GET  /login                 login / first-run setup page
  GET  /api/authstate         {setup_needed, setup_allowed, user}
  POST /api/login {user, password}       sets the session cookie
  POST /api/setup {user, password}       first run only, loopback clients only
With a session:
  POST /api/logout
  POST /api/password {old, new}          change your own password (ends your sessions)
  GET  /api/admins                       {users, me}
  POST /api/admins {user, password}      add an admin account
  DELETE /api/admins?user=U              remove one (not yourself, not the last one; ends its sessions)

API (all JSON):
  GET  /api/status            server time, today's tournaments, series, online users per lobby
  GET  /api/gamedata          course / rule / club / ball / character names (data/gamedata.json)
  GET  /api/settings          full settings.json
  GET  /api/defaults          factory defaults (used by the "reset to default" buttons)
  PUT  /api/settings          replace settings.json (validated; lobby changes rebuild appdata.xb)
  GET  /api/calendar?days=N   resolved tournaments for the next N days
  GET  /api/scores            all stored Daily results
  GET  /api/procs             server processes (via supervisor.py)
  GET  /api/log?name=N&after=SEQ&max=M   captured console output of a process
  POST /api/proc?name=N&action=start|stop|restart
  POST /api/shutdown          stop the whole server (supervisor and all processes)
  POST /api/wrap {text, width}     preview of the server-side word wrap used for news / agreement
  DELETE /api/scores?board=B&player=P   remove one result (player optional = whole board)
"""
import base64, datetime, http.server, json, os, socketserver, threading, urllib.parse, urllib.request

import activity, auth, settings
from config import DAY_OFFSET

ROOT = os.path.dirname(os.path.abspath(__file__))
BIND = os.environ.get("HSG6_ADMIN_BIND", "0.0.0.0")
PORT = int(os.environ.get("HSG6_ADMIN_PORT", "18080"))
MEDIUS_STATUS = os.environ.get("HSG6_MEDIUS_STATUS", "http://127.0.0.1:10081/status")
SUPERVISOR = os.environ.get("HSG6_SUPERVISOR", "http://127.0.0.1:18081")
APP_ID = 24224
JST = datetime.timezone(datetime.timedelta(hours=9))


def now_jst():
    return datetime.datetime.now(JST) + datetime.timedelta(days=DAY_OFFSET)


def medius_status():
    try:
        with urllib.request.urlopen(MEDIUS_STATUS, timeout=2) as r:
            return json.load(r)
    except Exception as e:  # noqa
        return {"error": f"Medius status unavailable ({e.__class__.__name__}): is Server.Medius running?"}


def status():
    data = settings.load()
    today = now_jst().date()
    med = medius_status()
    users, lobbies, rooms = [], {}, []
    if "error" not in med:
        users = [c for c in med["clients"] if c.get("loggedIn") and c.get("appId") == APP_ID]
        for g in med["games"]:
            if g.get("appId") != APP_ID:
                continue
            ln = settings.lobby_name(g["name"], data)
            if ln:
                key = f'{ln["club"]} / {ln["floor"]} / {ln["lobby"]}'
                e = lobbies.setdefault(key, {**ln, "players": [], "games": 0})
                e["players"] += g["players"]
                e["games"] += 1
            else:
                rooms.append(g)
    # per clubhouse / floor totals
    tree = {}
    for e in lobbies.values():
        c = tree.setdefault(e["club"], {"count": 0, "floors": {}, "club_i": e["club_i"]})
        f = c["floors"].setdefault(e["floor"], {"count": 0, "lobbies": {}, "floor_i": e["floor_i"]})
        f["lobbies"][e["lobby"]] = {"count": len(e["players"]), "map": e["map"], "players": e["players"]}
        f["count"] += len(e["players"])
        c["count"] += len(e["players"])
    for u in users:
        ln = settings.lobby_name(u.get("gameName"), data)
        u["where"] = (f'{ln["club"]} / {ln["floor"]} / {ln["lobby"]}' if ln
                      else (f'Room: {u["gameName"]}' if u.get("gameName") else "Lobby selection / menus"))
    sid, sname, sst, sen = settings.series_for(today, data)
    return {
        "server_time_jst": now_jst().isoformat(timespec="seconds"),
        "day_offset": DAY_OFFSET,
        "today": today.isoformat(),
        "series": {"id": sid, "name": sname, "start": sst.isoformat(), "end": sen.isoformat()},
        "tournaments_today": [{"slot": s, "ruleset": r, "label": rs.get("label", r),
                               "board": f"Daily_{today:%Y%m%d}_{s:02d}"}
                              for s, r, rs in settings.tournaments_for(today, data)],
        "medius_error": med.get("error"),
        "users": users,
        "user_count": len(users),
        "tree": tree,
        "rooms": rooms,
        "http_activity": activity.snapshot(),
        "public_port": int(os.environ.get("HSG6_PUBLIC_PORT", "18082")),
    }


def supervisor(path, method="GET"):
    """Proxy to supervisor.py's control API (process list, logs, start/stop/restart)."""
    try:
        req = urllib.request.Request(SUPERVISOR + path, method=method, data=b"" if method == "POST" else None)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except Exception as e:  # noqa
        return {"error": f"supervisor unavailable ({e.__class__.__name__}); servers were not started with start_background.bat"}


def calendar(days):
    data = settings.load()
    start = now_jst().date()
    out = []
    for i in range(days):
        d = start + datetime.timedelta(days=i)
        out.append({"date": d.isoformat(), "override": d.isoformat() in data["daily"]["overrides"],
                    "series": settings.series_for(d, data)[1],
                    "tournaments": [{"slot": s, "ruleset": r} for s, r, _ in settings.tournaments_for(d, data)]})
    return out


def scores_path():
    return os.path.join(ROOT, "data", "scores.json")


def load_scores():
    try:
        return json.load(open(scores_path()))
    except (OSError, ValueError):
        return {}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _client_is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _login_routes(self, method, path):
        """Routes that work without a session. Returns True when handled."""
        if method == "GET" and path == "/theme.css":
            self._send(200, ctype="text/css; charset=utf-8", raw=open(os.path.join(ROOT, "admin", "theme.css"), "rb").read())
        elif method == "GET" and path == "/login":
            self._send(200, ctype="text/html; charset=utf-8", raw=open(os.path.join(ROOT, "admin", "login.html"), "rb").read())
        elif method == "GET" and path == "/api/authstate":
            need = auth.account() is None
            self._send(200, {"setup_needed": need, "setup_allowed": need and self._client_is_local(),
                             "user": auth.session_user(self.headers.get("Cookie"))})
        elif method == "POST" and path == "/api/login":
            b = self._body()
            try:
                token = auth.login(self.client_address[0], b.get("user"), b.get("password"))
            except PermissionError as e:
                self._send(403, {"error": str(e)})
            else:
                self._send(200, {"ok": True}, headers={"Set-Cookie": auth.cookie(token)})
        elif method == "POST" and path == "/api/setup":
            if auth.account() is not None:
                self._send(409, {"error": "an admin account already exists"})
            elif not self._client_is_local():
                self._send(403, {"error": "create the admin account from the server machine itself"})
            else:
                b = self._body()
                auth.set_password(b.get("user"), b.get("password"))
                token = auth.login(self.client_address[0], b.get("user"), b.get("password"))
                self._send(200, {"ok": True}, headers={"Set-Cookie": auth.cookie(token)})
        else:
            return False
        return True

    def _send(self, code, obj=None, ctype="application/json", raw=None, headers=None):
        data = raw if raw is not None else json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if method != "GET" and self.headers.get("X-HSG6") != "1":
                return self._send(403, {"error": "missing X-HSG6 header"})
            if self._login_routes(method, u.path):
                return
            user = auth.session_user(self.headers.get("Cookie"))
            if not user:
                if method == "GET" and u.path in ("/", "/index.html"):
                    return self._send(302, raw=b"", headers={"Location": "/login"})
                if not u.path.startswith("/img/"):
                    return self._send(401, {"error": "login required"})
            if method == "POST" and u.path == "/api/logout":
                auth.logout(self.headers.get("Cookie"))
                return self._send(200, {"ok": True}, headers={"Set-Cookie": auth.cookie("", 0)})
            if method == "GET" and u.path == "/api/admins":
                return self._send(200, {"users": auth.users(), "me": user})
            if method == "POST" and u.path == "/api/admins":
                b = self._body()
                auth.add_user(b.get("user"), b.get("password"))
                return self._send(200, {"ok": True, "users": auth.users()})
            if method == "DELETE" and u.path == "/api/admins":
                auth.remove_user(q.get("user"), user)
                return self._send(200, {"ok": True, "users": auth.users()})
            if method == "POST" and u.path == "/api/password":
                b = self._body()
                if not auth.check_password(user, b.get("old")):
                    return self._send(403, {"error": "current password is wrong"})
                auth.set_password(user, b.get("new"))
                return self._send(200, {"ok": True}, headers={"Set-Cookie": auth.cookie("", 0)})
            if method == "GET" and u.path in ("/", "/index.html"):
                html = open(os.path.join(ROOT, "admin", "index.html"), "rb").read()
                return self._send(200, ctype="text/html; charset=utf-8", raw=html)
            if method == "GET" and u.path.startswith("/img/"):
                name = os.path.basename(u.path)
                path = os.path.join(ROOT, "admin", "img", name)
                if not os.path.isfile(path):
                    return self._send(404, {"error": "no such image"})
                ctype = "image/png" if name.endswith(".png") else "image/jpeg"
                return self._send(200, ctype=ctype, raw=open(path, "rb").read())
            if method == "GET" and u.path == "/api/status":
                return self._send(200, status())
            if method == "GET" and u.path == "/api/gamedata":
                return self._send(200, json.load(open(os.path.join(ROOT, "data", "gamedata.json"), encoding="utf-8")))
            if method == "GET" and u.path == "/api/settings":
                return self._send(200, settings.load())
            if method == "PUT" and u.path == "/api/settings":
                n = int(self.headers.get("Content-Length") or 0)
                new = json.loads(self.rfile.read(n))
                old = settings.load()
                settings.save(new)
                if new["lobby"] != old["lobby"]:
                    settings.write_lobby_csvs(new)
                    settings.rebuild_appdata()
                return self._send(200, {"ok": True, "lobby_rebuilt": new["lobby"] != old["lobby"]})
            if method == "GET" and u.path == "/api/defaults":
                return self._send(200, settings.DEFAULTS)
            if method == "POST" and u.path == "/api/wrap":
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n))
                return self._send(200, {"text": settings.wrap_text(body.get("text", ""), body.get("width", 34))})
            if method == "GET" and u.path == "/api/procs":
                return self._send(200, supervisor("/procs"))
            if method == "GET" and u.path == "/api/log":
                return self._send(200, supervisor("/log?" + urllib.parse.urlencode(q)))
            if method == "POST" and u.path == "/api/shutdown":   # stops every process, this one too
                return self._send(200, supervisor("/shutdown", "POST"))
            if method == "POST" and u.path == "/api/proc" and q.get("action") in ("start", "stop", "restart"):
                return self._send(200, supervisor(f"/{q['action']}?name={urllib.parse.quote(q.get('name', ''))}", "POST"))
            if method == "GET" and u.path == "/api/calendar":
                return self._send(200, calendar(min(int(q.get("days", 14)), 120)))
            if method == "GET" and u.path == "/api/scores":
                return self._send(200, load_scores())
            if method == "DELETE" and u.path == "/api/scores":
                db = load_scores()
                b, p = q.get("board"), q.get("player")
                if p:
                    db.get(b, {}).pop(p, None)
                else:
                    db.pop(b, None)
                tmp = scores_path() + ".tmp"
                json.dump(db, open(tmp, "w"), indent=1)
                os.replace(tmp, scores_path())
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except (ValueError, KeyError, TypeError) as e:
            return self._send(400, {"error": str(e)})

    def do_GET(self):
        self._route("GET")

    def do_PUT(self):
        self._route("PUT")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start():
    srv = S((BIND, PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="admin").start()
    print(f"HSG6 admin page on http://{BIND}:{PORT}/ (login required)", flush=True)
    return srv


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["set-password"]:  # python admin.py set-password <user>
        import getpass
        user = sys.argv[2] if len(sys.argv) > 2 else input("Admin user name: ")
        pw = getpass.getpass("New password (min 8 chars): ")
        if pw != getpass.getpass("Repeat: "):
            sys.exit("passwords differ")
        auth.set_password(user, pw)
        print(f"Saved {auth.AUTH_FILE}. {user}'s old sessions were logged out.")
        sys.exit(0)
    start()  # standalone (without the game HTTP server)
    threading.Event().wait()
