"""HSG6 (NPUA80864) replacement for cmnap.scej-online.jp HTTP services.

Serves static files from ./www and logs every request (headers + body) to ./logs.
Game-API handlers are registered in HANDLERS keyed by path.
"""
import http.server, socketserver, os, sys, time, datetime, urllib.parse, threading

ROOT = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(ROOT, "www")
LOGDIR = os.path.join(ROOT, "logs")
PORT = int(os.environ.get("HSG6_PORT", "18000"))
_lock = threading.Lock()
_seq = 0

HANDLERS = {}  # path -> fn(handler, query:dict, body:bytes) -> (status, ctype, bytes)


def log_request(h, body):
    global _seq
    with _lock:
        _seq += 1
        n = _seq
    stamp = datetime.datetime.now().strftime("%H%M%S")
    base = os.path.join(LOGDIR, f"{stamp}_{n:04d}")
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(f"{h.command} {h.path} {h.request_version}\n")
        for k, v in h.headers.items():
            f.write(f"{k}: {v}\n")
        f.write(f"\n[{len(body)} bytes body]\n")
    if body:
        open(base + ".bin", "wb").write(body)
    print(f"[{stamp}] #{n} {h.command} {h.path} body={len(body)}", flush=True)


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, status, ctype, data):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        body = self._body()
        log_request(self, body)
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        if u.path.startswith("/hsg6ps3/gameapi/"):
            import importlib, gameapi
            try:
                importlib.reload(gameapi)  # hot-reload handlers while developing
            except Exception as e:  # noqa
                print(f"    reload failed: {e}", flush=True)
            fn = gameapi.make_endpoint(u.path.rsplit("/", 1)[1])
        elif u.path.startswith("/hsg6ps3/real"):
            import importlib, realapi
            try:
                importlib.reload(realapi)
            except Exception as e:  # noqa
                print(f"    reload failed: {e}", flush=True)
            name = u.path.rsplit("/", 1)[1]
            if name.startswith("real"):  # old ss.info without trailing slash
                name = name[4:]
            fn = realapi.endpoint(name) if name.endswith(".cgi") else None
        else:
            fn = None
        if fn is None:
            fn = HANDLERS.get(u.path)
        if fn:
            st, ct, data = fn(self, q, body)
            return self._send(st, ct, data)
        p = os.path.normpath(os.path.join(WWW, u.path.lstrip("/")))
        if p.startswith(WWW) and os.path.isfile(p):
            return self._send(200, "application/octet-stream", open(p, "rb").read())
        print(f"    -> 404 {u.path}", flush=True)
        self._send(404, "text/plain", b"not found")

    do_GET = do_POST = do_PUT = _handle

    def log_message(self, *a):
        pass


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    os.makedirs(LOGDIR, exist_ok=True)
    try:
        import gameapi  # noqa: registers handlers
        gameapi.register(HANDLERS)
    except ImportError:
        pass
    import activity  # noqa: keep one instance across gameapi hot-reloads
    import admin, public
    admin.start()
    public.start()
    print(f"HSG6 HTTP server on :{PORT}, www={WWW}", flush=True)
    S(("0.0.0.0", PORT), H).serve_forever()
