"""In-memory record of recent game-API activity (who called what, when) for the admin page.

Imported once by hsg6_http.py; gameapi.py is hot-reloaded per request but this module is not, so
the data survives reloads. Lost on server restart (the scores themselves live in data/)."""
import datetime, threading

_lock = threading.Lock()
players = {}   # name -> {"last": iso time, "endpoint": str, "count": int}
requests = []  # last 200 (time, player, endpoint)


def seen(player, endpoint):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        if player:
            p = players.setdefault(player, {"count": 0})
            p.update(last=now, endpoint=endpoint, count=p["count"] + 1)
        requests.append((now, player, endpoint))
        del requests[:-200]


def snapshot():
    with _lock:
        return {"players": dict(players), "requests": list(requests[-50:])}
