"""Editable server settings (data/settings.json), shared by gameapi.py and the admin page.

Everything an operator may want to change lives here:
  news / eula          - Information screen and user agreement
  series               - tour periods ("tournament series"); TP is summed per series
  rulesets             - named tournament rule sets (course, holes, tee, rules, restrictions)
  daily                - which rule set the Daily Int'l Tournament uses, per date and slot
  lobby                - clubhouse / floor / lobby tree (written to the appdata CSVs)

The file is re-read on every call (cheap; keeps the hot-reloading game API and the admin page in
sync without restarts). Writes are atomic.
"""
import copy, datetime, json, os, threading

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "data", "settings.json")
_lock = threading.Lock()

# Lobby limits enforced by the client tables (see the Protocol wiki page, "Lobby layout CSVs").
MAX_CLUBS, MAX_FLOORS, MAX_LOBBIES = 2, 10, 20
LOBBY_MAPS = [8, 9, 10, 11, 12, 13]  # xbdata/lobby/lobbyNN.xb shipped with the game

DEFAULT_RULESET = {
    "label": "Standard",
    "course": 1,            # regulation +0x54 (1..20; 0 = Practice crashes)
    "holes": 0,             # +0x55 holes mode (0=18, 1..3=9, 4=6, 5=3)
    "tee": 0,               # +0x56 (0 regular, 1 long tee / mirror)
    "rule1": 0,             # +0x5c special rule (Rule_Rule id; 0 = None)
    "rule2": 0,             # +0x5d
    "rule3": 0,             # +0x5e
    "rule4": 0,             # +0x5f
    "character": 255,       # +0x60 (255 = any)
    "club": 255,            # +0x61 (255 = any)
    "ball": 255,            # +0x62 (255 = any)
    # Raw regulation bytes (see the Protocol wiki page "Regulation fields traced in code"):
    # b44[0] "tour" flag, u4e round random seed (0 = automatic per-day seed), b50_0_3[0] event format (0-5), b57 rule-manager
    # value (+1), b58_5b[1] (0x59) online advancement cut %, b58_5b[3] (0x5b) unknown gate;
    # the rest has no reader in the game. Kept at the values known to work.
    "advanced": {"b44": [0, 0, 0, 0], "u48": 0, "b4c": [0, 0], "u4e": 0,
                 "b50_0_3": [0, 1, 1, 1], "b57": 0, "b58_5b": [0, 0, 0, 0], "b63_65": [1, 1, 1]},
}

DEFAULTS = {
    "news": [{"title": "Welcome back!",
              "body": "The Hot Shots Golf: World Invitational\nonline service is running on a\ncommunity server."}],
    "eula": {"version": 1,
             "text": "HSG6 community revival server.\nThis is an unofficial fan server.\n"
                     "Scores and profiles may be reset\nat any time."},
    # Neither in-game box wraps text, so the server wraps it (see wrap_text). Widths are characters
    # per line: 34 is known to fit the narrow agreement box, ~60 fits the Information box.
    # news_title_marker: put before every news title, like the original server ("■ Title")
    "format": {"news_width": 60, "eula_width": 34, "news_title_marker": "■ "},
    "series": [],           # [{"id","name","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}]; none = calendar month
    "rulesets": {"standard": DEFAULT_RULESET},
    "daily": {
        "default": [{"slot": 1, "ruleset": "standard"}],   # used for every date without an override
        "overrides": {},    # {"YYYY-MM-DD": [{"slot": 1..3, "ruleset": id}, ...]}
    },
    "lobby": {"clubhouses": [
        {"name": "Animals", "floors": ["Cat", "Tiger", "Elephant", "Rhino", "Giraffe", "Bear", "Dog",
                                       "Sheep", "Cow", "Horse"],
         "lobbies": [{"name": f"Lobby {i + 1}", "map": m, "capacity": 30} for i, m in enumerate(LOBBY_MAPS)]},
        {"name": "Flowers", "floors": ["Buttercup", "Carnation", "Iris", "Poppy", "Cherry Blossom", "Tulip",
                                       "Crocus", "Primrose", "Lily of the Valley", "Bellflower"],
         "lobbies": [{"name": f"Lobby {i + 1}", "map": m, "capacity": 30} for i, m in enumerate(LOBBY_MAPS)]},
    ]},
}


def load():
    with _lock:
        try:
            data = json.load(open(PATH, encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    out = copy.deepcopy(DEFAULTS)
    out.update(data)
    _one_per_day(out)
    return out


def _one_per_day(data):
    """The game shows exactly one Daily Int'l Tournament: AddBoard (0x2dec8) stores a single
    'current board' index (+0x3ff8), so extra boards on the same day are never offered. Older
    settings with several tournaments per day keep only the first, always as slot 1."""
    daily = data.get("daily", {})
    for key in ["default"] + [f"overrides/{d}" for d in daily.get("overrides", {})]:
        day = daily["default"] if key == "default" else daily["overrides"][key.split("/", 1)[1]]
        if day:
            first = sorted(day, key=lambda t: int(t.get("slot", 1)))[0]
            day[:] = [{"slot": 1, "ruleset": first["ruleset"]}]


def save(data):
    validate(data)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with _lock:
        tmp = PATH + ".tmp"
        json.dump(data, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        os.replace(tmp, PATH)


def validate(data):
    missing = [k for k in DEFAULTS if k not in data]
    if missing:
        raise ValueError(f"missing settings: {', '.join(missing)}")
    for rid, rs in data.get("rulesets", {}).items():
        if not 1 <= int(rs.get("course", 1)) <= 20:
            raise ValueError(f"ruleset {rid}: course must be 1..20 (0 = Practice crashes the round)")
        if not 0 <= int(rs.get("holes", 0)) <= 5:
            raise ValueError(f"ruleset {rid}: holes mode must be 0..5")
    days = [data["daily"]["default"], *data["daily"]["overrides"].values()]
    for day in days:
        if len(day) != 1:
            raise ValueError("each day has exactly one Daily tournament (the game only shows one per day)")
        if int(day[0]["slot"]) != 1:
            raise ValueError("the Daily tournament must use slot 1 (slot 0 results never upload)")
        for t in day:
            if t["ruleset"] not in data["rulesets"]:
                raise ValueError(f"unknown ruleset {t['ruleset']!r}")
    clubs = data["lobby"]["clubhouses"]
    if not 1 <= len(clubs) <= MAX_CLUBS:
        raise ValueError(f"1..{MAX_CLUBS} clubhouses")
    for c in clubs:
        if not 1 <= len(c["floors"]) <= MAX_FLOORS:
            raise ValueError(f"{c['name']}: 1..{MAX_FLOORS} floors")
        if not 1 <= len(c["lobbies"]) <= MAX_LOBBIES:
            raise ValueError(f"{c['name']}: 1..{MAX_LOBBIES} lobbies")
        for lb in c["lobbies"]:
            if int(lb["map"]) not in LOBBY_MAPS:
                raise ValueError(f"lobby map must be one of {LOBBY_MAPS} (other maps are not installed)")
    for s in data.get("series", []):
        datetime.date.fromisoformat(s["start"]), datetime.date.fromisoformat(s["end"])


# ---------------- text formatting ----------------

def wrap_text(text, width):
    """Word-wrap free text for the in-game boxes: keeps the author's line breaks and blank lines,
    re-flows each line to `width` characters, splits words longer than a line."""
    import textwrap
    width = max(10, int(width))
    out = []
    for line in str(text).splitlines():
        line = " ".join(line.split())
        out += textwrap.wrap(line, width, break_long_words=True, break_on_hyphens=True) or [""]
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def news_title(title, marker):
    title = " ".join(str(title).split())
    return title if not marker or title.startswith(marker.strip()) else marker + title


def news_items(data=None):
    """[(title, body)] for getinfo. The game lays the Information screen out itself (parser
    0x527554): 3 full-width spaces + title, blank line, every body line indented by 5 full-width
    spaces, 3 blank lines between items. The server only adds the title marker and wraps the body."""
    data = data or load()
    fmt = data.get("format", {})
    w, marker = fmt.get("news_width", 60), fmt.get("news_title_marker", "■ ")
    return [(news_title(n["title"], marker), wrap_text(n["body"], w)) for n in data["news"]]


def eula(data=None):
    data = data or load()
    return int(data["eula"]["version"]), wrap_text(data["eula"]["text"], data.get("format", {}).get("eula_width", 34))


# ---------------- daily tournaments ----------------

def tournaments_for(date, data=None):
    """[(slot, ruleset_id, ruleset)] for a date."""
    data = data or load()
    day = data["daily"]["overrides"].get(date.isoformat(), data["daily"]["default"])
    return [(int(t["slot"]), t["ruleset"], data["rulesets"][t["ruleset"]]) for t in sorted(day, key=lambda t: int(t["slot"]))]


def daily_seed(date, slot=1):
    """Round random seed (+0x4e, u16) for a day: stable for everyone on that date, different per day.
    It drives the hole shuffle and the round RNG (the Protocol wiki page). Never 0."""
    import hashlib
    h = hashlib.sha1(f"hsg6-daily-{date.isoformat()}-{slot}".encode()).digest()
    return int.from_bytes(h[:2], "big") or 1


def regulation_fields(rs, date=None, slot=1):
    """Ruleset -> the byte groups of the regulation line (see gameapi._reg_line).
    A seed of 0 in the rule set means 'automatic': a per-day seed from daily_seed()."""
    a = {**DEFAULT_RULESET["advanced"], **rs.get("advanced", {})}
    if not int(a.get("u4e", 0)) and date is not None:
        a["u4e"] = daily_seed(date, slot)
    b50 = (*a["b50_0_3"], int(rs["course"]), int(rs["holes"]))
    b56 = (int(rs["tee"]), a["b57"], *a["b58_5b"], int(rs["rule1"]), int(rs["rule2"]), int(rs["rule3"]),
           int(rs["rule4"]), int(rs["character"]), int(rs["club"]), int(rs["ball"]), *a["b63_65"])
    return dict(b44=tuple(a["b44"]), u48=a["u48"], b4c=tuple(a["b4c"]), u4e=a["u4e"], b50=b50, b56=b56)


# ---------------- series ----------------

def series_for(date, data=None):
    """(id, name, start, end) of the series containing date; calendar month if none is configured."""
    data = data or load()
    for s in data.get("series", []):
        st, en = datetime.date.fromisoformat(s["start"]), datetime.date.fromisoformat(s["end"])
        if st <= date <= en:
            return s["id"], s["name"], st, en
    st = date.replace(day=1)
    en = (st + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    return f"{date:%Y-%m}", f"{date:%B %Y} Tour", st, en


# ---------------- lobby layout -> appdata CSVs ----------------

def write_lobby_csvs(data=None):
    """club_house.csv / floor.csv / lobby.csv (no header, ',' separated; see the Protocol wiki page)."""
    data = data or load()
    ad = os.path.join(ROOT, "appdata")
    clubs = data["lobby"]["clubhouses"]
    club = "".join(f"{i + 1},{c['name']}\n" for i, c in enumerate(clubs))
    floor = "".join(f"{j + 1},{f},0,0\n" for c in clubs for j, f in enumerate(c["floors"]))
    lobby = "".join(f"{k + 1},{int(lb['map'])},{lb['name']},1,{int(lb.get('capacity', 30))}\n"
                    for c in clubs for k, lb in enumerate(c["lobbies"]))
    for n, t in (("club_house.csv", club), ("floor.csv", floor), ("lobby.csv", lobby)):
        open(os.path.join(ad, n), "w", encoding="utf-8", newline="").write(t)


def rebuild_appdata():
    import subprocess, sys
    subprocess.run([sys.executable, os.path.join(ROOT, "make_appdata.py")], check=True, cwd=ROOT,
                   capture_output=True)


def lobby_name(game_name, data=None):
    """Medius game name '%02d%02d%02dU%02d' (club, floor, lobby, map; 0-based) -> readable parts."""
    import re
    m = re.fullmatch(r"(\d\d)(\d\d)(\d\d)U(\d\d)", game_name or "")
    if not m:
        return None
    c, f, l, mp = map(int, m.groups())
    data = data or load()
    clubs = data["lobby"]["clubhouses"]
    club = clubs[c] if c < len(clubs) else None
    return {
        "club": club["name"] if club else f"#{c}",
        "floor": club["floors"][f] if club and f < len(club["floors"]) else f"#{f}",
        "lobby": club["lobbies"][l]["name"] if club and l < len(club["lobbies"]) else f"#{l}",
        "map": mp, "club_i": c, "floor_i": f, "lobby_i": l,
    }
