"""gameapi_* endpoint handlers (see hsgproto.py for the wire format).

Response layouts are taken from the client parsers in the EBOOT; the
function address of each parser is noted per handler.
"""
import datetime, hashlib, os, re, secrets, struct, threading
from config import DAY_OFFSET
import activity, settings
from hsgproto import (el, el_u32, el_i32, el_str, el_bcd_datetime, bcd, envelope, encode_response,
                      decode_request, parse, dump)

JST = datetime.timezone(datetime.timedelta(hours=9))
# EULA and news text come from settings.json (editable on the admin page). Neither box wraps
# text, so settings.wrap_text() breaks it into lines before sending.



def now_jst():
    """Server clock in JST, shifted by config.DAY_OFFSET days."""
    return datetime.datetime.now(JST) + datetime.timedelta(days=DAY_OFFSET)


_sessions = {}
_lock = threading.Lock()


def new_session():
    sid = secrets.token_hex(6)
    with _lock:
        _sessions[sid] = {"created": datetime.datetime.now()}
    return sid


def any_session(req):
    # The client sends its own encrypted token; we don't need to validate it.
    return new_session()


# ---- handlers: fn(req_elems) -> tlv bytes ----

def h_geteula(req):
    """parser 0x524f30: 1050 ver, 1000, 1010 md5, 1030 len, 1020 text.
    The request's 1050 is the version the player already accepted (0 = never; saved from our
    1050 at global+0xb869c). Result 2/3 = nothing new, so the agreement is not shown again."""
    version, text = settings.eula()
    if _req_u32(req, 0x1050, 0) == version:
        return envelope(result=2)
    text = text.encode("utf-8")
    return envelope(
        el_u32(0x1050, version),
        el(0x1000),
        el(0x1010, hashlib.md5(text).digest()),
        el_u32(0x1030, len(text)),
        el(0x1020, text + b"\0"),
    )


def h_login(req):
    """parser 0x5261f0: [token] 2020 status(!=5), 2030 str (opt), 2021 u32"""
    return envelope(
        el_u32(0x2020, 1),
        el_u32(0x2021, 0),
        session=any_session(req),
    )


def h_getservertime(req):
    """parser 0x525e3c: [token] 5000 BCD datetime (JST)"""
    return envelope(el_bcd_datetime(0x5000, now_jst()), session=any_session(req))


def h_getinfo(req):
    """parser 0x527554: [token] 3000 count, {3010, 3030 title, 3040 body, 3020}*"""
    news = settings.news_items()  # word-wrapped to the box width
    body = [el_u32(0x3000, len(news))]
    for title, text in news:
        body += [el(0x3010), el_str(0x3030, title), el_str(0x3040, text), el(0x3020)]
    return envelope(*body, session=any_session(req))


def _bcd_date(d):
    return bytes([bcd(d.year // 100), bcd(d.year % 100), bcd(d.month), bcd(d.day)])


def board_item(index, name, start, end, rid=0, extra="", desc=""):
    """getboardlist item (parser 0x529d88 -> AddBoard 0x2dec8, list at global+0x4058, 0x4c/entry):
    4140 key 'xxx_<index>' (index <= 420) | 4150 name (entry+0) | 4151 str | 4152 | 4176 u32 |
    4177 start BCD yyyy mm dd | 4178 end BCD | 41a4 str. A board whose end date is after today
    is required by the Daily-tournament check at 0x2e274."""
    return [el(0x4120), el_str(0x4140, f"Board_{index}"), el_str(0x4150, name), el_str(0x4151, extra),
            el(0x4152), el_u32(0x4176, rid), el(0x4177, _bcd_date(start)), el(0x4178, _bcd_date(end)),
            el_str(0x41a4, desc), el(0x4130)]


# Daily slots (low 4 bits of the regulation id) come from settings.daily (1..3 per day). The save
# keeps results per slot; the type-7 upload (0x532c1c) only walks slots 1..3, so slot 0 never uploads.


def current_boards():
    """[(board index, board name, start, end, regulation id, ruleset)] for today."""
    today = now_jst().date()
    out = []
    for slot, _, rs in settings.tournaments_for(today):
        rid = reg_id(today.month, today.day, slot)
        # The client keeps the first name it sees per board index (AddBoard 0x2dec8), so every
        # day gets its own index range (1..420). Slot 1 keeps the index used before multi-slot support.
        idx = ((today - datetime.date(2026, 1, 1)).days + 140 * (slot - 1)) % 420 + 1
        out.append((idx, f"Daily_{today:%Y%m%d}_{slot:02d}", today, today + datetime.timedelta(days=1), rid, rs))
    return out


def h_getboardlist(req):
    """parser 0x529d88: [token] 4100 count, 4110 u32, items (see board_item)."""
    boards = current_boards()
    body = [el_u32(0x4100, len(boards)), el_u32(0x4110, len(boards))]
    for idx, name, start, end, rid, _ in boards:
        body += board_item(idx, name, start, end, rid)
    return envelope(*body, session=any_session(req))


def h_getsignboardlist(req):
    """parser 0x532668: [token] 4100 count, {4120,4140 s,4151,4160 ver,4161,4162,41a3 date,41a4 date,4130}* (max 4).
    Boards whose cached version/file is missing are then fetched via getsignboarddetail."""
    return envelope(el_u32(0x4100, 0), session=any_session(req))


def h_getsignboarddetail(req):
    """parser 0x531f18: [token] 4160 ver, 41e5 size, 41c5 data. size 0 = nothing stored."""
    return envelope(el_u32(0x4160, 0), el_u32(0x41e5, 0), el(0x41c5), session=any_session(req))


def reg_id(month, day, slot, year=None):
    """+0x40 packed id (decoded by 0x536514 as Daily_%04d%02d%02d_%02d):
    bits 28-31 year-2011, 24-27 month, 19-23 day, bit 4 Daily flag, 0-3 slot.
    The client sets bit 4 on the ids it stores after a round (0x532c1c), and the entry screen
    matches those records against this id ("already joined"), so the flag must be set here too."""
    if year is None:
        year = now_jst().year
    return (((year - 2011) & 0xf) << 28) | ((month & 0xf) << 24) | ((day & 0x1f) << 19) | 0x10 | (slot & 0xf)


def _reg_line(rid, name=None, b44=(0, 0, 0, 0), u48=0, b4c=(0, 0), u4e=0, b50=(0,) * 6, b56=(0,) * 16):
    """One regulation (0x68-byte struct, max 3), tab separated, parser 0x52afd8:
    f0 id u32 -> +0x40 | f1 name -> +0x00 | 4 bytes +0x44 |
    u32 +0x48 | 2 bytes +0x4c | u16 +0x4e | 6 bytes +0x50 | 16 bytes +0x56 | 2 ignored.
    +0x54 (b50[4]) < 7 for a normal entry; +0x5c/+0x5d shown in list."""
    f = [str(rid), name or str(rid), *map(str, b44), str(u48), *map(str, b4c), str(u4e), *map(str, b50), *map(str, b56), "0", "0"]
    return "\t".join(f)


# Regulation byte fields come from the ruleset (settings.regulation_fields). Known meanings:
#   0x54 course, 0x55 holes mode (0=18, 1-3=9, 4=6, 5=3), 0x56 tee/mirror, 0x5c-0x5f special rules
#   (Rule_Rule ids; Official needs Rule byte +4 == 0 for 0x5c/0x5d), 0x60 character (0xff = any,
#   else rule 0x57+v), 0x61 club (rule 0x17+v), 0x62 ball (rule 0x34+v). Others unknown.


def h_getregulationdata(req):
    """parser 0x52afd8 (type 19): [token] 6000 = text (opt. UTF-8 BOM), lines split, fields by delimiter.
    Empty text = zero regulations (success)."""
    lines = [_reg_line(rid, **settings.regulation_fields(rs, start, rid & 0xf))
             for _, _, start, _, rid, rs in current_boards()]
    text = "".join(l + "\n" for l in lines).encode()
    return envelope(el(0x6000, text + bytes(4)), session=any_session(req))


def _req_str(req, tag):
    for t, d in req:
        if t == tag:
            return d.split(b"\0")[0].decode("utf-8", "replace")
    return ""


def _req_bytes(req, tag):
    return next((d for t, d in req if t == tag), b"")


def _req_u32(req, tag, default=0):
    for t, d in req:
        if t == tag and len(d) >= 4:
            return struct.unpack_from("<I", d)[0]
    return default


# ---- score store: data/scores.json {board: {player: {score, points, holes, t}}} ----
_SCORES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scores.json")


def _load_scores():
    import json
    try:
        return json.load(open(_SCORES))
    except (OSError, ValueError):
        return {}


def _save_scores(db):
    import json
    os.makedirs(os.path.dirname(_SCORES), exist_ok=True)
    tmp = _SCORES + ".tmp"
    json.dump(db, open(tmp, "w"), indent=1)
    os.replace(tmp, _SCORES)


def parse_daily_result(blob):
    """regscore 0xad8c payload: 8-byte header, then TLV: 4501 (u32 points, u32), 38b4 = u32 hash +
    0x46nn stat strings (nn 0..0x1b) + 0x47nn per-hole score vs par (nn 0..17), 8010."""
    from hsgproto import parse as tlv
    out = {"points": 0, "stats": {}, "holes": [0] * 18}
    for t, d in tlv(blob[8:]):
        if t == 0x4501 and len(d) >= 4:
            out["points"] = struct.unpack_from("<i", d)[0]
        elif t == 0x38b4:
            for t2, d2 in tlv(d[4:]):
                v = d2.split(b"\0")[0].decode("ascii", "replace")
                if t2 >> 8 == 0x46:
                    out["stats"][t2 & 0xff] = v
                elif t2 >> 8 == 0x47 and (t2 & 0xff) < 18:
                    out["holes"][t2 & 0xff] = int(v or 0)
    out["score"] = sum(out["holes"])
    return out


def h_getscorelist(req):
    """getscorelist: 4140 list name, 4000 first rank, 4010 count. Variants:
    Daily_<id>_score (type 9, parser 0x52700c): score histogram -> 4200 N, 4210, 4220 (=0), then N x
      {4250, 4270, 4500 s16 score vs par, 4501 u16 ?, 4502 u32 players with that score, 4260}.
      The game sums these into Player Count / Average Score (0x54160c) and Temp. Rank.
    Other lists (Daily_<id>_holescore, PlayerRanking_001, PointRanking) are returned empty for now."""
    name = _req_str(req, 0x4140)
    body = []
    if re.fullmatch(r"Daily_\d{8}_\d\d", name):
        if _req_u32(req, 0x4010, 1) > 1:
            return _board_ranking_list(req, name)
        return _finished_result(req, name)
    if name.startswith("Daily_") and name.endswith("_score"):
        board = name[:-len("_score")]
        hist = {}
        for e in _load_scores().get(board, {}).values():
            hist[e["score"]] = hist.get(e["score"], 0) + 1
        for sc in sorted(hist):
            body += [el(0x4250), el(0x4270), el(0x4500, struct.pack("<h", sc)), el(0x4501, struct.pack("<H", 0)),
                     el_u32(0x4502, hist[sc]), el(0x4260)]
        n = len(hist)
    elif name.startswith("PlayerRanking"):
        return _player_ranking(req)
    elif name == "PointRanking":
        return _point_ranking(req)
    else:
        n = 0
    return envelope(el_u32(0x4200, n), el_u32(0x4210, n), el_u32(0x4220, 0), *body, session=any_session(req))


def _player(req):
    return _req_str(req, 0x20) or "?"


def tour_points(db=None, today=None):
    """Tour = the series containing today (settings.series; calendar month if none).
    TP = sum of Daily points per player over the boards dated inside the series."""
    db = _load_scores() if db is None else db
    today = today or now_jst().date()
    _, _, start, end = settings.series_for(today)
    tp = {}
    for board, players in db.items():
        try:
            d = datetime.datetime.strptime(board[6:14], "%Y%m%d").date()
        except ValueError:
            continue
        if start <= d <= end:
            for p, e in players.items():
                tp[p] = tp.get(p, 0) + int(e.get("points", 0))
    return tp


def _player_ranking(req):
    """PlayerRanking_001 (types 12/13, parser 0x53a5a8): 4200, 4210 u32 total, 4220 u32 (0 = no entry),
    4230, 4270 u32 rank, 4500 8 bytes (lo u32, hi u32 -> global+0xb86b0, shown as TP). Stored per board."""
    tp = tour_points()
    me = _player(req)
    if _req_u32(req, 0x4010, 1) > 1:
        return _player_ranking_list(req, tp, me)
    if me not in tp:
        return envelope(el_u32(0x4200, 0), el_u32(0x4210, len(tp)), el_u32(0x4220, 0), session=any_session(req))
    rank = 1 + sum(1 for v in tp.values() if v > tp[me])
    return envelope(el_u32(0x4200, 1), el_u32(0x4210, len(tp)), el_u32(0x4220, 1), el(0x4230),
                    el_u32(0x4270, rank), el(0x4500, struct.pack("<II", tp[me], tp[me])), session=any_session(req))


def _profile_stats(player, n=0x16):
    """0x4600.. run for a ranking row (parser needs >= 15): last uploaded PointRanking stats."""
    import json
    try:
        st = json.load(open(_STATS)).get(player, {}).get("stats", {})
    except (OSError, ValueError):
        st = {}
    return [el_str(0x4600 + k, st.get(str(k), "0")) for k in range(n)]


def _point_values():
    """Cumulative shot points per player: low u32 of the 4500 value from the last PointRanking regscore."""
    import json
    try:
        db = json.load(open(_STATS))
    except (OSError, ValueError):
        return {}
    return {p: struct.unpack_from("<I", bytes.fromhex(r["v4500"]).ljust(8, bytes(1)))[0] for p, r in db.items()}


def _player_ranking_list(req, tp, me):
    """PlayerRanking list (types 17/18, parser 0x527fcc), requested with 4010 = page size:
    4200 N, 4210 u32 total, 4220 u32 (nonzero = own row follows: 4230, 4270 rank, 4500, 0x4600.. run,
    then 0x4240), then N x {4250, 4270 rank, 4500 8 bytes (lo/hi = TP), 0x4600.. (>=15), 0x20 name, 4260}."""
    first = max(1, _req_u32(req, 0x4000, 1))
    count = _req_u32(req, 0x4010, 100)
    order = sorted(tp.items(), key=lambda kv: -kv[1])
    ranks, prev, r = {}, None, 0
    for i, (p, v) in enumerate(order):
        if v != prev:
            r, prev = i + 1, v
        ranks[p] = r
    page = order[first - 1:first - 1 + count]
    body = [el_u32(0x4200, len(page)), el_u32(0x4210, len(order))]
    if me in tp:
        body += [el_u32(0x4220, 1), el(0x4230), el_u32(0x4270, ranks[me]),
                 el(0x4500, struct.pack("<II", tp[me], tp[me])), *_profile_stats(me), el(0x4240)]
    else:
        body.append(el_u32(0x4220, 0))
    for p, v in page:
        body += [el(0x4250), el_u32(0x4270, ranks[p]), el(0x4500, struct.pack("<II", v, v)),
                 *_profile_stats(p), el_str(0x20, p.encode(), minlen=12), el(0x4260)]
    return envelope(*body, session=any_session(req))


_STATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pointranking.json")


def _point_ranking(req):
    """PointRanking (type 15, parser 0x528c0c): 4200, 4210, 4220 (0 = none), 4230, 4270, 4500.. (>=1),
    then 0x4600.. stat strings (the 38b4 fields last uploaded by regscore PointRanking); field 0x13 is
    parsed as a number and handed back to the caller."""
    import json
    if _req_u32(req, 0x4010, 1) > 1:   # Prize King list (same list format as the tour ranking)
        return _player_ranking_list(req, _point_values(), _player(req))
    try:
        rec = json.load(open(_STATS)).get(_player(req))
    except (OSError, ValueError):
        rec = None
    if not rec:
        return envelope(el_u32(0x4200, 0), el_u32(0x4210, 0), el_u32(0x4220, 0), session=any_session(req))
    pv = _point_values()
    mine = pv.get(_player(req), 0)
    rank = 1 + sum(1 for v in pv.values() if v > mine)
    body = [el_u32(0x4200, 1), el_u32(0x4210, len(pv)), el_u32(0x4220, 1), el(0x4230), el_u32(0x4270, rank),
            el(0x4500, bytes.fromhex(rec["v4500"]))]
    # parser 0x528c0c requires a consecutive run starting at 0x4600; uploads start at 0x4601
    stats = dict(rec["stats"])
    for k in range(0, max(map(int, stats)) + 1 if stats else 0):
        body.append(el_str(0x4600 + k, stats.get(str(k), "0")))
    return envelope(*body, session=any_session(req))


def _store_point_ranking(req):
    import json
    from hsgproto import parse as tlv
    v4500 = next((d for t, d in req if t == 0x4500), bytes(8))
    blob = next((d for t, d in req if t == 0x38b4), None)
    if blob is None:
        return
    stats = {}
    for t2, d2 in tlv(blob[4:]):
        if t2 >> 8 == 0x46:
            stats[str(t2 & 0xff)] = d2.split(bytes(1))[0].decode("ascii", "replace")
    try:
        db = json.load(open(_STATS))
    except (OSError, ValueError):
        db = {}
    db[_player(req)] = {"v4500": v4500.hex(), "stats": stats}
    os.makedirs(os.path.dirname(_STATS), exist_ok=True)
    json.dump(db, open(_STATS, "w"), indent=1)


def _save_req(kind, req):
    import json
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"), exist_ok=True)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{kind}.jsonl"), "a") as f:
        f.write(json.dumps({"t": datetime.datetime.now().isoformat(),
                            "elems": [[hex(t), d.hex()] for t, d in req]}) + "\n")


def h_regscore(req):
    """regscore (types 7/14/16; parsers 0x534644, 0x5265ac, 0x527d34):
    [token] 4270 u32 (returned to caller), 42a0, 8010. Requests are logged to data/regscore.jsonl.
    Daily results (4140 'Daily_<id>', 0xad8c blob) are stored per player in data/scores.json."""
    _save_req("regscore", req)
    name = _req_str(req, 0x4140)
    blob = next((d for t, d in req if t == 0xad8c), None)
    if name == "PointRanking":
        _store_point_ranking(req)
    if name.startswith("Daily_") and not blob and _req_bytes(req, 0x4500):
        # Direct upload right after the round (type 11, 0x534aa8): 4500 score s32, 4501 points u32.
        player = _req_str(req, 0x20) or _req_str(req, 0x42b0).split("@")[0]
        db = _load_scores()
        if player not in db.get(name, {}):
            score = struct.unpack_from("<i", _req_bytes(req, 0x4500))[0]
            points = struct.unpack_from("<I", _req_bytes(req, 0x4501) or bytes(4))[0]
            db.setdefault(name, {})[player] = {"score": score, "points": points, "holes": [], "stats": {},
                                               "t": datetime.datetime.now().isoformat(timespec="seconds")}
            _save_scores(db)
            print(f"    [regscore] stored {name} {player}: score {score:+d}, {points} pts (direct)", flush=True)
    if name.startswith("Daily_") and blob:
        try:
            res = parse_daily_result(blob)
            player = _req_str(req, 0x20) or _req_str(req, 0x42b0).split("@")[0]
            db = _load_scores()
            prev = db.get(name, {}).get(player)
            if prev and not prev.get("holes"):
                # Fill in hole/stat detail for a result first stored by the direct upload.
                prev.update(holes=res["holes"], stats=res["stats"])
                _save_scores(db)
                return envelope(el_u32(0x4270, 0), el(0x42a0), session=any_session(req))
            if prev:
                # One entry per board: keep the first result (the client blocks re-entry the same way).
                print(f"    [regscore] {name} {player}: already has a result, ignoring re-upload", flush=True)
                return envelope(el_u32(0x4270, 0), el(0x42a0), session=any_session(req))
            db.setdefault(name, {})[player] = {"score": res["score"], "points": res["points"],
                                               "holes": res["holes"], "stats": res["stats"],
                                               "t": datetime.datetime.now().isoformat(timespec="seconds")}
            _save_scores(db)
            print(f"    [regscore] stored {name} {player}: score {res['score']:+d}, {res['points']} pts", flush=True)
        except Exception as e:  # noqa
            print(f"    [regscore] could not decode daily result: {e}", flush=True)
    return envelope(el_u32(0x4270, 0), el(0x42a0), session=any_session(req))


# Boards listed as finished even before their end date (testing aid; empty for normal play).
FINISH_EARLY = set()


def _board_end(board):
    """Daily_YYYYMMDD_NN runs one day: ends at midnight JST the next day."""
    try:
        d = datetime.datetime.strptime(board[6:14], "%Y%m%d").replace(tzinfo=JST)
    except ValueError:
        return None
    return d + datetime.timedelta(days=1)


def finished_boards(player):
    now = now_jst()
    out = []
    for board, players in _load_scores().items():
        end = _board_end(board)
        if player in players and end and (end <= now or board in FINISH_EARLY):
            out.append(board)
    return sorted(out)


def h_getfinishboardlist(req):
    """parser 0x53cac0: [token] 6540, 6560 count A, 6580 list A, 6550, 6570 count B, 6590 list B, 8010.
    Lists are concatenated 17-char board names 'Daily_YYYYMMDD_NN' (parsed by 0x525ae8 with
    'Daily_%4d%2d%2d_%2d'). List A entries are skipped by the client when it already holds a result
    record without the 'requested' flag; list B entries are always taken. For each listed board the
    client then requests its final result (type 21, sender 0x53bc60 -> _finished_result)."""
    boards = finished_boards(_player(req))
    blob = "".join(boards).encode()
    return envelope(el(0x6540), el_u32(0x6560, 0), el(0x6580, bytes(1)), el(0x6550),
                    el_u32(0x6570, len(boards)), el(0x6590, blob + bytes(1)), session=any_session(req))


def _finished_result(req, board):
    """Final result of a finished board (type 21, parser 0x53c340): 4200, 4210 u32 total players,
    4220 nonzero, 4230, 4270 u32 rank, 4500 (score s32, points u32), 4501, then 0x4600.. strings
    (0x460f-0x4613, 0x4615, 0x461b are stored in the save record; meaning unknown -> 0; 0x4614 = TP).
    Stored at record +0x44 score, +0x48 points, +0x4c rank, +0x50 players; status 4 = finished."""
    players = _load_scores().get(board, {})
    me = _player(req)
    if me not in players:
        return envelope(el_u32(0x4200, 0), el_u32(0x4210, len(players)), el_u32(0x4220, 0),
                        session=any_session(req))
    e = players[me]
    rank = 1 + sum(1 for p in players.values() if p["score"] < e["score"])
    body = [el_u32(0x4200, 1), el_u32(0x4210, len(players)), el_u32(0x4220, 1), el(0x4230),
            el_u32(0x4270, rank), el(0x4500, struct.pack("<iI", e["score"], e["points"])),
            el(0x4501, struct.pack("<II", e["points"], 0))]
    # 0x4614 = TP earned on this board (record +0x54; summed into Career TP / Best TP).
    stats = {0x14: str(e["points"])}
    body += [el_str(0x4600 + k, stats.get(k, "0")) for k in range(0x1c)]
    return envelope(*body, session=any_session(req))


def _board_ranking_list(req, board):
    """Rankings list of one board (opened from Tournament Results; parser 0x527fcc, same list format
    as _player_ranking_list). 4500 = (score s32, points u32), 4501 = (points, 0) as in _finished_result.
    Lower score ranks first, ties broken by points."""
    players = _load_scores().get(board, {})
    me = _player(req)
    first = max(1, _req_u32(req, 0x4000, 1))
    count = _req_u32(req, 0x4010, 100)
    order = sorted(players.items(), key=lambda kv: (kv[1]["score"], -int(kv[1].get("points", 0))))
    ranks, prev, r = {}, None, 0
    for i, (p, e) in enumerate(order):
        if e["score"] != prev:
            r, prev = i + 1, e["score"]
        ranks[p] = r

    def vals(e):
        pts = int(e.get("points", 0))
        return [el(0x4500, struct.pack("<iI", e["score"], pts)), el(0x4501, struct.pack("<II", pts, 0))]

    page = order[first - 1:first - 1 + count]
    body = [el_u32(0x4200, len(page)), el_u32(0x4210, len(order))]
    if me in players:
        body += [el_u32(0x4220, 1), el(0x4230), el_u32(0x4270, ranks[me]), *vals(players[me]),
                 *_profile_stats(me), el(0x4240)]
    else:
        body.append(el_u32(0x4220, 0))
    for p, e in page:
        body += [el(0x4250), el_u32(0x4270, ranks[p]), *vals(e), *_profile_stats(p),
                 el_str(0x20, p.encode(), minlen=12), el(0x4260)]
    return envelope(*body, session=any_session(req))


HANDLERS = {
    "getfinishboardlist": h_getfinishboardlist,
    "regscore": h_regscore,
    "getscorelist": h_getscorelist,
    "getregulationdata": h_getregulationdata,
    "getsignboardlist": h_getsignboardlist,
    "getsignboarddetail": h_getsignboarddetail,
    "getboardlist": h_getboardlist,
    "geteula": h_geteula,
    "login": h_login,
    "getservertime": h_getservertime,
    "getinfo": h_getinfo,
}


def make_endpoint(name):
    def endpoint(http, query, body):
        try:
            tlv = decode_request(body)
            req = parse(tlv)
        except Exception as e:  # noqa
            print(f"    [{name}] request decode failed: {e}")
            req = []
        print(f"    [{name}] request:\n{dump(req)}", flush=True)
        activity.seen(_req_str(req, 0x20), name)
        fn = HANDLERS.get(name)
        if fn is None:
            print(f"    [{name}] NOT IMPLEMENTED -> error result", flush=True)
            tlv_out = envelope(result=0)
        else:
            tlv_out = fn(req)
        print(f"    [{name}] response:\n{dump(parse(tlv_out))}", flush=True)
        return 200, "application/octet-stream", encode_response(tlv_out)
    return endpoint


API_NAMES = ["login", "geteula", "getinfo", "getboardlist", "getscorelist", "regscore",
             "uploaddata", "getservertime", "getregulationdata", "getfinishboardlist",
             "getsignboardlist", "getsignboarddetail"]


def register(handlers):
    for n in API_NAMES:
        handlers[f"/hsg6ps3/gameapi/{n}"] = make_endpoint(n)
