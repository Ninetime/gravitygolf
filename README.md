# Gravity Golf: a Hot Shots Golf: World Invitational revival server

Gravity Golf brings back the online modes of **Hot Shots Golf: World Invitational**
(Everybody's Golf 6, PS3, **NPUA80864**). Sony shut down the official servers years ago, and no
public replacement existed. This project is a from-scratch replacement for those servers. You
play through the [RPCS3](https://rpcs3.net) emulator.

## What works

- **Going online:** bootstrap (`ss.info`), EULA, login, server time and the news / Information
  screen.
- **Daily International Tournament:**
  - a new board every day (course, holes, tee and rules are set on the admin page)
  - score upload, with re-entry blocked once you've played
  - Tournament History and Results
- **Rankings:**
  - tour standings (Player Ranking) and the Prize King / Shot Point Ranking
  - per-board rankings, including friends-only mode
  - the end-of-tour ceremony
- **Lobby:** clubhouses, floors and lobbies, walking around with your avatar, free and easy
  chat, room creation and player search.
- **Admin page:** news, EULA, tournament series, rule sets, the daily schedule, the lobby layout,
  results management, server status and logs, all behind a login.
- **Public status page:** players online per lobby, results and tour standings, and the
  upcoming schedule. It shows no online names or locations.

Known gap: the in-lobby **Friends List** softlocks. That is an RPCS3 bug (`sceNpFriendlist` never
calls its result handler), not a server bug. See `hsg6/docs/CHANGES.md` §5.

## How it was made

Nothing about the original protocol was documented, so it was all **reverse engineered** from the
game:
- static analysis of the game executable in Ghidra
- live memory inspection of the running game through RPCS3's PINE interface
- logging every request the game sends

The work was done **with the assistance of AI (Claude, by Anthropic)**, which helped with:
- disassembly and decompilation analysis
- working out the encryption and message formats
- writing and testing the server code
- debugging against the running game
- writing the documentation

Findings were checked against the real game at each step. `hsg6/docs/PROTOCOL.md` records what
was learned, including the addresses in the game executable where each piece of behaviour lives.

## Layout

| path | contents |
|---|---|
| `hsg6/server/` | Python HTTP servers: `ss.info`, the game API, the "real" API, app data, the admin page and the public page |
| `hsg6/medius/`, `muis/`, `dme/`, `nat/` | config files for the [Horizon](https://github.com/Horizon-Private-Server/horizon-server) Medius servers (lobby, login, game worlds and NAT); the binaries are built from source, see below |
| `hsg6/docs/` | `PROTOCOL.md` (reversed protocol), `OPERATIONS.md` (running and hosting), `CHANGES.md` (third-party changes) and `horizon-server.patch` |
| `hsg6/supervisor.py`, `*.bat` | start, stop and keep all servers running |
| `src/`, `Cargo.toml` | an earlier Rust prototype that only logged the game's requests; not needed to run the server |

## Building

### Requirements

- A legally obtained copy of the game (**NPUA80864**, updated to **v01.03**) installed in
  RPCS3. Several data files are extracted from it rather than shipped here.
- Python 3.10 or newer (standard library only).
- The [.NET 10 SDK](https://dotnet.microsoft.com/download) and git, to build the Horizon servers.
- Windows. The `.bat` scripts and the supervisor assume it, but the Python and .NET parts are
  cross-platform.

### 1. Generate the game-derived files

`<USRDIR>` is the game's folder inside RPCS3, for example
`D:/Games/rpcs3/dev_hdd0/game/NPUA80864/USRDIR`.

```sh
cd hsg6/server
python tools/extract_appdata.py <USRDIR>       # rule table, chat text, easy-chat phrase list
python tools/extract_gamedata.py <USRDIR>      # course / rule / club names for the admin page
python tools/extract_admin_images.py <USRDIR>  # images for the admin and public pages
python -c "import settings; settings.write_lobby_csvs()"   # default lobby layout
python make_appdata.py                          # builds www/hsg6ps3/real/files/appdata.xb
python make_ssinfo.py                           # builds www/hsg6ps3/ss.info
```

### 2. Build the Horizon Medius servers

The Medius servers come from upstream Horizon, with a patch for this game's older network library
(details in `hsg6/docs/CHANGES.md` §1):

```sh
git clone https://github.com/Horizon-Private-Server/horizon-server hsg6/thirdparty/horizon-server
cd hsg6/thirdparty/horizon-server
git checkout b1a0d0398f58b2d2b72ffc122053e788e794185a
git apply ../../docs/horizon-server.patch

dotnet build Server.UniverseInformation/Server.UniverseInformation.csproj -c Release
dotnet build Server.Medius/Server.Medius.csproj -c Release
dotnet build Server.Dme/Server.Dme.csproj -c Release
dotnet build Server.NAT/Server.NAT.csproj -c Release
```

Copy each project's `bin/Release/net10.0/*` into the matching folder:
- `Server.UniverseInformation` goes to `hsg6/muis/`
- `Server.Medius` goes to `hsg6/medius/`
- `Server.Dme` goes to `hsg6/dme/`
- `Server.NAT` goes to `hsg6/nat/`

Keep the `*.json` config files that are already in those folders. Don't let the build's own copies
overwrite them.

### 3. Run

```bat
cd hsg6
start_background.bat    &rem all servers, hidden, with auto-restart (stop_all.bat stops them)
```

You can use `start_all.bat` instead, which opens each server in its own console window.

Then open the admin page at <http://127.0.0.1:18080/> on the server machine and create the first
admin account. From there you can set the news, rule sets, daily schedule and lobby layout. Until
you first save, built-in defaults are used. The public status page is at
<http://127.0.0.1:18082/>.

## Connecting with RPCS3

1. Add this to `patches/NPUA80864_patch.yml` and enable it in the Patch Manager. It points the
   game at plain HTTP on port 18000. If the file already exists, add only the `PPU-…` entry:
   ```yaml
   Version: 1.2

   PPU-cdf4e1c581ae975cd6943427043cd527da6937a4:     # NPUA80864 v01.03
     "Server patching":
       Games:
         "Everybody's Golf":
           NPUA80864: [01.03]
       Author: "Ninetime"
       Patch Version: 1.0
       Patch:
         - [utf8, 0x00bfd8c0, "http://cmnap.scej-online.jp:18000/hsg6ps3/ss.info\0"]
   ```
2. In the game's custom configuration, under Network → **IP swap list**, add
   `cmnap.scej-online.jp=<server IP>`. Use `127.0.0.1` if the server runs on the same PC.
3. Set Network to Connected and PSN to RPCN. Your RPCN name becomes your in-game name.

## Hosting for other players

To host for players on other machines:
- Set the public address in `server/config.py` (`HSG6_MEDIUS_HOST`), then re-run
  `make_ssinfo.py`.
- Set it in `muis/muis.json`, and as `PublicIpOverride` / `NATIp` in `medius/medius.json` and
  `dme/dme.json`.
- Forward the game ports.

`hsg6/docs/OPERATIONS.md` §3 has the full list. It also covers which ports must **not** be
exposed, the admin page's security model, the data files to back up, and troubleshooting.

## Credits

- Server, protocol research and documentation by **Ninetime**, with AI assistance from
  **Claude** (Anthropic).
- [Horizon Private Server](https://github.com/Horizon-Private-Server/horizon-server) for the Medius
  server implementation.
- The [RPCS3](https://rpcs3.net) and [RPCN](https://github.com/RipleyTom/rpcn) projects.

Hot Shots Golf / Everybody's Golf is a trademark of Sony Interactive Entertainment. This project
is not affiliated with or endorsed by Sony. It contains no game files: everything game-derived
is extracted from your own copy.
