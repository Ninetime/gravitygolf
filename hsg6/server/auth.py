"""Admin login: admin accounts stored as salted PBKDF2 hashes in data/admin_auth.json, session
tokens in an HttpOnly cookie, and a per-address lockout after repeated wrong passwords.

First run: with no account file, the login page offers "create admin account", accepted only
from the server machine itself (loopback). Sessions are saved to data/admin_sessions.json (as
SHA-256 hashes of the tokens, never the tokens themselves), so restarting the http process does
not log anyone out.
More accounts are added and removed on the admin page's Settings tab. Reset a forgotten password
(or create an account) from a console with
    python admin.py set-password <user>
"""
import hashlib, hmac, json, os, secrets, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(ROOT, "data", "admin_auth.json")
SESSION_FILE = os.path.join(ROOT, "data", "admin_sessions.json")
COOKIE = "hsg6_admin"
SESSION_TTL = 12 * 3600          # seconds a login stays valid
ITERATIONS = 240_000
MAX_FAILS, LOCK_SECONDS = 5, 300  # 5 wrong passwords -> address locked for 5 minutes

_lock = threading.Lock()
_sessions = None                  # sha256(token) -> [user, expires]; loaded on first use
_fails = {}                       # address -> (count, locked_until)


def _tid(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _load_sessions():
    """Caller holds _lock."""
    global _sessions
    if _sessions is None:
        try:
            _sessions = json.load(open(SESSION_FILE, encoding="utf-8"))
        except (OSError, ValueError):
            _sessions = {}
    now = time.time()
    for k in [k for k, v in _sessions.items() if v[1] < now]:
        del _sessions[k]
    return _sessions


def _save_sessions():
    """Caller holds _lock."""
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_sessions, f)
    os.replace(tmp, SESSION_FILE)


def _hash(password, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()


def _load_accounts():
    """{user: {salt, iterations, hash}}. Reads the old single-account format too."""
    try:
        d = json.load(open(AUTH_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if "users" in d:
        return d["users"]
    if "user" in d:                       # format before multiple accounts
        return {d["user"]: {k: d[k] for k in ("salt", "iterations", "hash")}}
    return {}


def _save_accounts(users):
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    tmp = AUTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f, indent=1)
    os.replace(tmp, AUTH_FILE)


def account():
    """Truthy when at least one admin account exists."""
    return _load_accounts() or None


def users():
    return sorted(_load_accounts())


def _drop_sessions(user):
    """Log out every session of one user (all sessions when user is None)."""
    with _lock:
        ss = _load_sessions()
        for k in [k for k, v in ss.items() if user is None or v[0] == user]:
            del ss[k]
        _save_sessions()


def set_password(user, password, create=True):
    """Create an account or set its password; that user's sessions end."""
    user = " ".join((user or "").split())
    if not user or len(user) > 32:
        raise ValueError("user name required (at most 32 characters)")
    if len(password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    accts = _load_accounts()
    if user not in accts and not create:
        raise ValueError(f"no admin account named {user}")
    salt = secrets.token_hex(16)
    accts[user] = {"salt": salt, "iterations": ITERATIONS, "hash": _hash(password, salt)}
    _save_accounts(accts)
    _drop_sessions(user)


def add_user(user, password):
    user = " ".join((user or "").split())
    if user in _load_accounts():
        raise ValueError(f"an admin account named {user} already exists")
    set_password(user, password)


def remove_user(user, me):
    accts = _load_accounts()
    if user not in accts:
        raise ValueError(f"no admin account named {user}")
    if user == me:
        raise ValueError("you can't remove the account you are logged in with")
    if len(accts) == 1:
        raise ValueError("the last admin account can't be removed")
    del accts[user]
    _save_accounts(accts)
    _drop_sessions(user)


def check_password(user, password):
    rec = _load_accounts().get(" ".join((user or "").split()))
    # hash even for unknown users so the response time doesn't reveal which names exist
    good = _hash(password or "", rec["salt"] if rec else "00", rec.get("iterations", ITERATIONS) if rec else ITERATIONS)
    return bool(rec) and hmac.compare_digest(good, rec["hash"])


def locked(addr):
    with _lock:
        n, until = _fails.get(addr, (0, 0))
        return max(0, int(until - time.time()))


def login(addr, user, password):
    """-> session token, or raises PermissionError with a message for the page."""
    wait = locked(addr)
    if wait:
        raise PermissionError(f"Too many failed attempts. Try again in {wait} s.")
    if not check_password(user, password):
        with _lock:
            n = _fails.get(addr, (0, 0))[0] + 1
            _fails[addr] = (0, time.time() + LOCK_SECONDS) if n >= MAX_FAILS else (n, 0)
        time.sleep(0.5)
        raise PermissionError("Wrong user name or password.")
    token = secrets.token_urlsafe(32)
    with _lock:
        _fails.pop(addr, None)
        _load_sessions()[_tid(token)] = [" ".join((user or "").split()), time.time() + SESSION_TTL]
        _save_sessions()
    return token


def session_user(cookie_header):
    token = None
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            token = v
    if not token:
        return None
    with _lock:
        s = _load_sessions().get(_tid(token))
    # a removed account loses access at once, even if a session record survived
    return s[0] if s and s[0] in _load_accounts() else None


def logout(cookie_header):
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            with _lock:
                _load_sessions().pop(_tid(v), None)
                _save_sessions()


def cookie(token, max_age=SESSION_TTL):
    return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
