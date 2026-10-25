# Building

The repo includes the HSG6 server code and, as a git submodule, the source of the Medius servers
it uses ([Horizon](https://github.com/Horizon-Private-Server/horizon-server), pinned to the commit
the patch was made against). It doesn't include programs ready to run, or the data files that
come from the game. `build.py` sets up both in one step.

## Requirements

- A legally obtained copy of the game (**NPUA80864**, updated to **v01.03**) installed in
  RPCS3. Several data files are extracted from it rather than shipped here.
- Python 3.10 or newer (standard library only).
- The [.NET 10 SDK](https://dotnet.microsoft.com/download) and [git](https://git-scm.com/), to
  build the Medius servers.
- Windows. The `.bat` scripts and the supervisor assume it, but the Python and .NET parts are
  cross-platform.

## 1. Get the code

```sh
git clone --recursive -b hsg6-server https://github.com/Ninetime/gravitygolf.git
```

A plain clone or GitHub's "Download ZIP" also works: `build.py` fetches Horizon itself if the
submodule is missing.

## 2. Build

`<USRDIR>` is the game's folder inside RPCS3, for example
`D:/Games/rpcs3/dev_hdd0/game/NPUA80864/USRDIR`.

```sh
cd gravitygolf/hsg6
python build.py --usrdir <USRDIR>
```

This does the following:
1. **Medius servers:** fetches Horizon into `hsg6/thirdparty/horizon-server` and applies
   `docs/horizon-server.patch`, the fixes for this game's older network library (details in
   [Third-Party Changes](Third-Party-Changes.md) §1). It then builds the four servers and copies them into `muis/`,
   `medius/`, `dme/` and `nat/`. The config files already in those folders are kept.
2. **Game data:** extracts the game-derived files from your copy of the game, then builds
   `appdata.xb` and `ss.info`.

It's safe to run again, for example after updating:
- `python build.py` rebuilds only the servers.
- `python build.py --skip-horizon --usrdir <USRDIR>` redoes only the game data.

<details>
<summary>Doing it by hand instead</summary>

```sh
# Medius servers
cd hsg6/thirdparty/horizon-server            # git submodule update --init, if it's empty
git apply ../../docs/horizon-server.patch
dotnet build Server.UniverseInformation/Server.UniverseInformation.csproj -c Release
dotnet build Server.Medius/Server.Medius.csproj -c Release
dotnet build Server.Dme/Server.Dme.csproj -c Release
dotnet build Server.NAT/Server.NAT.csproj -c Release
```

Copy each project's `bin/Release/net10.0/*` into its folder:
- `Server.UniverseInformation` goes to `hsg6/muis/`
- `Server.Medius` goes to `hsg6/medius/`
- `Server.Dme` goes to `hsg6/dme/`
- `Server.NAT` goes to `hsg6/nat/`

```sh
# Game data
cd hsg6/server
python tools/extract_appdata.py <USRDIR>       # rule table, chat text, easy-chat phrase list
python tools/extract_gamedata.py <USRDIR>      # course / rule / club names for the admin page
python tools/extract_admin_images.py <USRDIR>  # images for the admin and public pages
python -c "import settings; settings.write_lobby_csvs()"   # default lobby layout
python make_appdata.py                          # builds www/hsg6ps3/real/files/appdata.xb
python make_ssinfo.py                           # builds www/hsg6ps3/ss.info
```

</details>

## 3. Run

```bat
cd hsg6
start_background.bat    &rem all servers, hidden, with auto-restart (stop_all.bat stops them)
```

You can use `start_all.bat` instead, which opens each server in its own console window.

Then open the admin page at <http://127.0.0.1:18080/> on the server machine and create the first
admin account. From there you can set the news, rule sets, daily schedule and lobby layout. Until
you first save, built-in defaults are used. The public status page is at
<http://127.0.0.1:18082/>.

## Next steps

- [Connecting with RPCS3](../README.md#connecting-with-rpcs3)
- [Operations](Operations.md): configuration, hosting for other players, the admin page, backups
  and troubleshooting.
