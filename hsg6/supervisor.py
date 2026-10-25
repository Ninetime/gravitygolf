"""HSG6 background supervisor: runs every server process without console windows, captures their
output to logs/<name>.log (rotated), restarts crashed processes, and exposes a small control API
for the admin page.

Start:  start_background.bat   (runs this with pythonw, no window)
Stop:   stop_all.bat           (POST /shutdown)

Control API (127.0.0.1:18081, localhost only; HSG6_SUPERVISOR_PORT to change):
  GET  /procs                        [{name, running, pid, started, restarts, exit_code, wanted}]
  GET  /log?name=N&after=SEQ&max=M   {"lines": [[seq, text], ...], "last": seq}  (in-memory tail)
  POST /start?name=N | /stop?name=N | /restart?name=N
  POST /shutdown                     stop everything and exit
Environment variables (HSG6_DAY_OFFSET, HSG6_PUBLIC_PORT, ...) are passed through to children.
"""
import collections, datetime, http.server, json, os, re, socketserver, subprocess, sys, threading, time, urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(ROOT, "logs")
PORT = int(os.environ.get("HSG6_SUPERVISOR_PORT", "18081"))
PY = os.path.join(os.path.dirname(sys.executable), "python.exe")  # console python for children (pythonw has no stdout)
MAX_LOG = 20 * 1024 * 1024          # rotate logs/<name>.log at 20 MB (one .old kept)
RING = 5000                         # lines kept in memory per process for the admin page
NO_WINDOW = 0x08000000              # CREATE_NO_WINDOW
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# name, working dir, command, seconds to wait after starting (dependency order)
PROCS = [
    ("http",   "server", [PY, "-u", "hsg6_http.py"], 1),
    ("muis",   "muis",   ["Server.UniverseInformation.exe"], 1),
    ("medius", "medius", ["Server.Medius.exe"], 5),   # DME registers with Medius' MPS
    ("dme",    "dme",    ["Server.Dme.exe"], 1),
    ("nat",    "nat",    ["Server.NAT.exe"], 0),
]


class Proc:
    def __init__(self, name, cwd, cmd):
        self.name, self.cwd, self.cmd = name, os.path.join(ROOT, cwd), cmd
        self.p = None
        self.started = None
        self.restarts = 0
        self.exit_code = None
        self.wanted = True          # False after a manual stop: no auto-restart
        self.lines = collections.deque(maxlen=RING)
        self.seq = 0
        self.lock = threading.Lock()
        self.logpath = os.path.join(LOGDIR, f"{name}.log")

    def log(self, text):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"{stamp} {text}"
        with self.lock:
            self.seq += 1
            self.lines.append((self.seq, line))
            try:
                if os.path.exists(self.logpath) and os.path.getsize(self.logpath) > MAX_LOG:
                    os.replace(self.logpath, self.logpath + ".old")
                with open(self.logpath, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def start(self):
        if self.running():
            return
        self.wanted = True
        exe = self.cmd[0] if os.path.isabs(self.cmd[0]) else os.path.join(self.cwd, self.cmd[0])
        self.log(f"[supervisor] starting {' '.join([exe] + self.cmd[1:])}")
        try:
            self.p = subprocess.Popen([exe] + self.cmd[1:], cwd=self.cwd, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      creationflags=NO_WINDOW, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        except OSError as e:
            self.log(f"[supervisor] failed to start: {e}")
            self.p = None
            return
        self.started = time.time()
        self.exit_code = None
        threading.Thread(target=self._pump, args=(self.p,), daemon=True).start()

    def _pump(self, p):
        for raw in iter(p.stdout.readline, b""):
            self.log(ANSI.sub("", raw.decode("utf-8", "replace").rstrip("\r\n")))
        p.wait()
        self.exit_code = p.returncode
        self.log(f"[supervisor] exited with code {p.returncode}")

    def stop(self):
        self.wanted = False
        if self.running():
            self.log("[supervisor] stopping")
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()

    def running(self):
        return self.p is not None and self.p.poll() is None

    def info(self):
        return {"name": self.name, "running": self.running(), "pid": self.p.pid if self.running() else None,
                "started": datetime.datetime.fromtimestamp(self.started).isoformat(timespec="seconds") if self.started else None,
                "restarts": self.restarts, "exit_code": self.exit_code, "wanted": self.wanted}


procs = {n: Proc(n, cwd, cmd) for n, cwd, cmd, _ in PROCS}
stopping = threading.Event()


def watchdog():
    """Restart processes that exit on their own (not after a manual stop), with a 5 s delay."""
    while not stopping.is_set():
        time.sleep(5)
        for pr in procs.values():
            if pr.wanted and not pr.running() and pr.p is not None and not stopping.is_set():
                pr.restarts += 1
                pr.log(f"[supervisor] auto-restart #{pr.restarts}")
                pr.start()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/procs":
            return self._send(200, [p.info() for p in procs.values()])
        if u.path == "/log" and q.get("name") in procs:
            pr = procs[q["name"]]
            after, mx = int(q.get("after", 0)), min(int(q.get("max", 500)), RING)
            with pr.lock:
                lines = [l for l in pr.lines if l[0] > after][-mx:]
                last = pr.seq
            return self._send(200, {"lines": lines, "last": last})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/shutdown":
            self._send(200, {"ok": True})
            threading.Thread(target=shutdown, daemon=True).start()
            return
        pr = procs.get(q.get("name"))
        if not pr or u.path not in ("/start", "/stop", "/restart"):
            return self._send(404, {"error": "unknown process or action"})
        # run in the background: restarting "http" kills the process serving the admin page
        def act():
            if u.path in ("/stop", "/restart"):
                pr.stop()
            if u.path in ("/start", "/restart"):
                time.sleep(1)
                pr.start()
        threading.Thread(target=act, daemon=True).start()
        self._send(200, {"ok": True})


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def shutdown():
    stopping.set()
    for pr in reversed(list(procs.values())):
        pr.stop()
    os._exit(0)


def main():
    os.makedirs(LOGDIR, exist_ok=True)
    try:
        srv = S(("127.0.0.1", PORT), H)       # also guards against a second supervisor
    except OSError:
        print(f"supervisor already running (port {PORT} in use)")
        return
    for n, _, _, delay in PROCS:
        procs[n].start()
        time.sleep(delay)
    threading.Thread(target=watchdog, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
