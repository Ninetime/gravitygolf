# Running and managing the HSG6 server

Paths are relative to `hsg6/`.

## 1. What runs where

| process | folder | ports | purpose |
|---|---|---|---|
| `python hsg6_http.py` | `server/` | TCP 18000 (game), TCP 18080 (admin, all addresses), TCP 18082 (public status page) | ss.info, game API, real API, app data, admin page, public page |
| `Server.UniverseInformation.exe` | `muis/` | TCP 10075 | Medius universe list |
| `Server.Medius.exe` | `medius/` | TCP 10079 (MAS), 10078 (MLS), 10077 (MPS, internal), 10081 (status, localhost) | Medius login and lobby |
| `Server.Dme.exe` | `dme/` | TCP 10073, UDP 50000 | lobby/room game worlds |
| `Server.NAT.exe` | `nat/` | UDP 10070 | NAT address discovery |

Start order: HTTP, MUIS, Medius, then DME (it registers with Medius on 10077), then NAT.

**Background (recommended):** `start_background.bat` runs `supervisor.py` with no window. The
supervisor starts all five processes hidden, writes their console output to `logs\<name>.log`
(rotated at 20 MB, one `.old` kept), restarts any process that exits on its own after 5 s, and
exposes a control API on 127.0.0.1:18081. Status, start/stop/restart and a live log viewer are on
the admin page's **Settings** tab. `stop_all.bat` stops everything. Starting the supervisor
twice is harmless (the second copy exits because port 18081 is taken).

**Foreground:** `start_all.bat` opens each process in its own console window instead. Close a
window to stop that process.

Requirements: Python 3.10+ (no extra packages), .NET 10 runtime, the `bf_tables.bin` file in
`server/`.

## 2. Client setup (each player, RPCS3)

1. Game: **NPUA80864**, updated to **v01.03**.
2. Patch: in RPCS3's Patch Manager, enable **Server patching** for the game. It comes from RPCS3's
   patch database; see [RPCS3 Patch](RPCS3-Patch.md).
3. Custom game config → Network → **IP swap list**: `cmnap.scej-online.jp=<server IP>`.
4. Network: connected, PSN = RPCN (any account; the RPCN name becomes the in-game name).
5. The Medius host the game uses comes from `ss.info` (`HSG6_MEDIUS_HOST`, below), not from the IP swap.

## 3. Hosting for other players

* `server/config.py` env vars: `HSG6_MEDIUS_HOST` = public IP/hostname of the Medius server (default
  `127.0.0.1`, only works on the same machine), `HSG6_PORT` (18000). Then run `python make_ssinfo.py` in
  `server/` to rebuild `www/hsg6ps3/ss.info`.
* `muis/muis.json` → `Universes.24224[0].Endpoint` = public address of the MAS (10079).
* `medius/medius.json` → `PublicIpOverride` = public IP. Medius hands this to clients for the MLS
  (10078). By default it uses an auto-detected LAN/VPN address.
* `dme/dme.json`: the DME announces its own IP; set `PublicIpOverride` in it as well when behind NAT.
* `medius/medius.json` → `NATIp` = public IP of the NAT server.
* Forward TCP 18000, 10073, 10075, 10078, 10079 and UDP 10070, 50000. Forward TCP 18082 too if you
  want the public status page reachable. **Do not** forward 10077, 10081 or 18081. Forwarding 18080 makes the admin page
  reachable from the internet; see the admin page section before doing that.

## 4. Configuration files

| file | key settings |
|---|---|
| `server/config.py` (env) | `HSG6_HTTP_HOST`, `HSG6_PORT`, `HSG6_MEDIUS_HOST`, `HSG6_MEDIUS_PORT`, `HSG6_DAY_OFFSET` |
| `server/admin.py` (env) | `HSG6_ADMIN_BIND` (0.0.0.0 = every address; `127.0.0.1` = this machine only), `HSG6_ADMIN_PORT` (18080), `HSG6_MEDIUS_STATUS` |
| `server/public.py` (env) | `HSG6_PUBLIC_BIND` (0.0.0.0), `HSG6_PUBLIC_PORT` (18082; `0` disables the public page) |
| `server/data/admin_auth.json` | admin login (user name + salted PBKDF2 hash); created on first login, not in git |
| `server/data/settings.json` | everything editable on the admin page (below) |
| `muis/muis.json` | `Ports`, `Universes.24224` |
| `medius/medius.json` | `MASPort`, `MLSPort`, `MPSPort`, `PublicIpOverride`, `NATIp/NATPort`, `StatusPort` (10081) |
| `dme/dme.json` | `ApplicationIds [24224]`, `TCPPort`, `UDPPort`, `MPS.Ip/Port` |
| `nat/nat.json` | `Port` |

`HSG6_DAY_OFFSET=N` shifts the server clock N days forward (testing only; e.g. to end today's
tournament early). Scores are stored under the shifted dates.

## 5. Admin page — "Galaxy Golf Server Admin" (`http://127.0.0.1:18080/`)

Served by the HTTP process. **Login required.**

* **First run:** open the page *on the server machine* (`http://127.0.0.1:18080/`). With no account
  yet it asks you to create one (user name + password, at least 8 characters). Account creation is
  refused from any other machine.
* Sessions last 12 hours (HttpOnly cookie) and survive restarts of the http process (saved in
  `data/admin_sessions.json`). 5 wrong passwords lock that address out for 5 minutes.
* The right end of the toolbar has **Public page ↗** (opens the public status page in a new tab;
  hidden when it is disabled) and the red **Log out** button. The header shows who is logged in
  (click it to open Settings).
* **Forgotten password:** run `python admin.py set-password <user>` in `server/` (it also creates
  the account if it doesn't exist). No restart needed.
* The admin page listens on every address of the machine (LAN, VPN and loopback), so you can open
  it from another computer at `http://<server-ip>:18080/`. The first account can still only be
  created from the server machine itself. The page is plain HTTP, so passwords cross the network
  unencrypted: use it over your LAN or VPN, don't forward port 18080 to the internet. To allow
  local access only, set `HSG6_ADMIN_BIND=127.0.0.1`.

* **Settings**:
  * change your own password (ends your other sessions); add and remove admin accounts (every
    account has full access; you can't remove the account you're logged in with or the last one;
    a removed account is logged out at once);
  * **Server processes**: state, PID, restart count, Start/Stop/Restart buttons, and **Shut down
    server** (asks you to type SHUTDOWN; stops every process including the admin and public pages;
    start again with `start_background.bat`). Needs the background supervisor;
  * **Log**: live console output of each process (filter by text or `/regex/`, optionally hiding
    Medius echo and keep-alive noise).
* **Status** (refreshes every 5 s): server time (JST), today's tournaments, the current series,
  players online (from Medius) with their location, player counts per clubhouse, floor and lobby,
  multiplayer rooms, and recent game-API activity.
* **Daily Tournaments**: the default rule set (used every day) and per-date overrides, plus a
  14-day preview. The game shows **one** Daily Int'l Tournament per day: AddBoard (`0x2dec8`) keeps a
  single "current board", so extra boards on the same day are never offered. Vary the rules
  day by day with overrides. Changes apply when players next open the menu. Don't change a day's
  rule set after players have entered it.
* **Rule Sets**: one editor; pick a rule set from the drop-down at the top to load its values.
  **New** asks for a name and starts from the default values; **Reset to default** restores the
  shown rule set; **Delete** removes it (not allowed while a day uses it). Fields: course, holes, tee,
  special rules 1–4, character/club/ball restriction, and (behind "Show advanced", off by default)
  raw unknown regulation bytes ([Protocol](Protocol.md) §2.5). The badge shows whether it
  will be Official (special rules 1 and 2 must be official rules). Course 0 (Practice) is rejected
  because it crashes the game.
* **Reset to default**: "Reset all rule sets to default" (Rule Sets tab), "reset to default" on a
  single rule set, and "Reset lobby layout to default" (Lobby Layout tab) load the factory values
  (`settings.DEFAULTS`) into the editor. Nothing changes until you press Save. Resetting all rule
  sets switches any daily tournament that used a removed rule set to `standard`.
* **Player names**: the game logs in with its name plus a trailing `#` (e.g. `Ninetime#`). The
  admin page hides the `#`; scores, rankings and logs keep the exact name.
* **Series**: a "Current tour" panel shows the tour in effect today and whether it is a configured
  series or the automatic calendar month ("Make it an editable series" copies it into the list).
  Below, configure tour periods (id, name, start, end). TP and the tour ranking add up Daily points
  over the series containing today; days outside every series use the calendar month.
  **Gaps:** if a series ends and no next series is configured, the fallback is the *whole*
  calendar month, which also includes the days of the series that just ended, so players' points
  carry over rather than starting again at 0. Add the next series before the current one ends.
* **Lobby Layout**: clubhouses (max 2), floors (max 10 per clubhouse), and lobbies (max 20 per
  clubhouse, shared by all its floors), each with a map (8–13) and capacity. Saving rewrites the
  CSVs and rebuilds `appdata.xb`; players get the new layout the next time they go online.
* **News & Agreement**: Information screen items and the user agreement. Type freely: the
  in-game boxes cannot wrap, so the server word-wraps the text when sending it (your own line
  breaks are kept). The agreement uses a fixed width of 34 characters, known to fit its narrow box.
  Information uses 60 characters (confirmed to fit in game). Both widths can still be changed in
  settings.json (`format.news_width`, `format.eula_width`) if ever needed. The page
  shows an exact preview. Raise the agreement version to make everyone accept it again.
  The game lays out Information itself, like the original service: title, blank line, body
  indented, items one after another in a single scrolling screen. "Marker before titles"
  (default `■ `, as the original service used) is added in front of every title; clear it if the
  glyph doesn't display in your game version.
* **Scores**: every stored Daily result, with delete per player or per board.

The page is a client of the JSON API in `admin.py` (documented at the top of that file), which can
also be scripted, e.g.
`curl -X PUT -H "Content-Type: application/json" --data @settings.json http://127.0.0.1:18080/api/settings`.
Invalid settings are rejected with an error message and nothing is written.

The page's artwork (sky background, clubhouse/floor/lobby frames and thumbnails) is extracted
from the game by `server/tools/extract_admin_images.py <USRDIR>` into `server/admin/img/`.

## 6. Public status page — "Galaxy Golf Server Status" (`http://<server>:18082/`)

Served by the HTTP process (`server/public.py`), read-only, no login. Shows:

* players online (total count), today's Daily tournament, the current tour;
* lobby usage: a clubhouse drop-down (defaults to the first clubhouse) and a floor × lobby table
  with player count and capacity, including empty lobbies, plus the number of multiplayer rooms
  and the players in them;
* tour standings (player, Tour Points) and Daily tournament results for the last 14 days
  (rank, player, score, points; today's board is marked "in progress");
* the schedule: the Daily tournaments of the next 14 days (rule set name, course, holes, tee),
  taken from the Daily Tournaments settings including per-day overrides.

**Privacy:** the page never shows who is online, where a player is, room names or hosts, or when a
result was uploaded, so it can't be used to watch when a particular person plays. Player names
appear only in the standings and results (as they do in the game's own rankings), without the
trailing `#`. Keep it that way when changing `public.py`.

**Not live:** all of it comes from one snapshot that the server rebuilds at most every 3 minutes,
no matter how many people have the page open (`public.py` `REFRESH`). The snapshot has an ETag;
browsers revalidate it and get `304 Not Modified` while nothing has changed. The page polls just
after the next snapshot is due and only redraws when the data actually changed ("last change"
in the header). The 3-minute delay also blurs exactly when someone came online.

## 7. Data files (`server/data/`)

| file | content | back up? |
|---|---|---|
| `settings.json` | admin-editable settings | yes |
| `scores.json` | Daily results `{board: {player: {score, points, holes, stats, t}}}` | yes |
| `pointranking.json` | last uploaded shot points and stats per player | yes |
| `regscore.jsonl` | raw log of every score upload | optional |
| `gamedata.json` | course/rule names (regenerate with `tools/extract_gamedata.py`) | no |
| `admin_auth.json` | admin accounts (salted password hashes). Never share it; delete it to get the first-run setup again | yes (private) |
| `admin_sessions.json` | active admin logins (hashed tokens); delete it to log everyone out | no (private) |

`server/logs/` holds every raw HTTP request (`HHMMSS_NNNN.txt/.bin`); delete it freely.

## 8. Rebuilding from a clean checkout

1. Install the game. Dump `EBOOT.elf` if you need to analyse it.
2. Run `python build.py --usrdir <USRDIR>` in `hsg6/` (see [Building](Building.md)). It does the following:
   - fetches the Horizon submodule (or clones it for a zip download) and applies
     `docs/horizon-server.patch`
   - builds MUIS, Medius, DME and NAT into `muis/`, `medius/`, `dme/`, `nat/`, next to the tracked
     json configs
   - runs `tools/extract_appdata.py`, `tools/extract_gamedata.py`, `tools/extract_admin_images.py`
     and `make_appdata.py` / `make_ssinfo.py`; the lobby CSVs are written from the defaults if
     missing
   - `--skip-horizon` does only the game-data part; leaving out `--usrdir` does only Horizon
3. `online_grade.csv`, `online_avatar_action.csv`, `online_item.csv` and `Rank_AutoHandi.txt`
   are hand-written and tracked (see [Protocol](Protocol.md) §4.2).
4. Start everything (`start_background.bat`). Open the admin page from the server machine to
   create the first admin account, then set news, rule sets and the lobby layout. Until the
   first save, `settings.py` defaults are used and `data/settings.json` doesn't exist.

## 9. Troubleshooting

| symptom | likely cause |
|---|---|
| "older version of the game" | `daily_ver/rank_ver/lobby_ver` in ss.info ≠ 1030 |
| `T-000000` when going online | `appdata.xb` missing a required file ([Protocol](Protocol.md) §4.2) |
| `R…` error | game API handler failed; check the HTTP window / `logs/` |
| `G…` error or "Communication error" in the lobby | Medius/DME not running, or a Medius reply layout mismatch ([Protocol](Protocol.md) §5) |
| crash on "Entering the lobby" | lobby CSVs missing or > 2 clubhouses; map not installed |
| Daily round crashes on load | course 0 or missing `Rule_Rule.txt` |
| Friends List hangs | RPCS3 `sceNpFriendlist` not implemented ([Third-Party Changes](Third-Party-Changes.md) §5) |
| Status page "Medius status unavailable" | Medius not running or `StatusPort` disabled |
