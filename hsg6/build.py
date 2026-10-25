"""Set up a fresh checkout: build the patched Horizon Medius servers and generate the game data.

Usage: python build.py [--usrdir USRDIR] [--skip-horizon]

  --usrdir USRDIR   also extract the game-derived files from an installed copy of the game
                    (<RPCS3 folder>/dev_hdd0/game/NPUA80864/USRDIR) and build appdata.xb
                    and ss.info. Without it only the Horizon servers are built.
  --skip-horizon    only do the --usrdir step.

Horizon steps (see the Third-Party Changes wiki page, section 1):
  1. fetch thirdparty/horizon-server at HORIZON_COMMIT (git submodule; plain clone for zip downloads)
  2. apply docs/horizon-server.patch (skipped when already applied)
  3. dotnet build each server and copy its output into muis/, medius/, dme/, nat/.
     The tracked *.json configs there are not part of the build output, so they are kept.
Requires git and the .NET 10 SDK. Safe to run again.
"""
import argparse, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
HORIZON = os.path.join(ROOT, "thirdparty", "horizon-server")
HORIZON_URL = "https://github.com/Horizon-Private-Server/horizon-server.git"
HORIZON_COMMIT = "b1a0d0398f58b2d2b72ffc122053e788e794185a"
PATCH = os.path.join(ROOT, "docs", "horizon-server.patch")
# Horizon project -> folder it runs from
SERVERS = [("Server.UniverseInformation", "muis"), ("Server.Medius", "medius"),
           ("Server.Dme", "dme"), ("Server.NAT", "nat")]


def run(cmd, cwd=None, check=True):
    print(">", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check)


def need(tool, hint):
    if not shutil.which(tool):
        sys.exit(f"'{tool}' was not found on PATH. {hint}")


def submodule_checkout():
    """(repo top, submodule path) when ROOT is in a git checkout that declares the Horizon
    submodule, else (None, None)."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        return None, None
    top = os.path.normpath(r.stdout.strip())
    sub = os.path.relpath(HORIZON, top).replace(os.sep, "/")
    try:
        declared = f"path = {sub}" in open(os.path.join(top, ".gitmodules"), encoding="utf-8").read()
    except OSError:
        declared = False
    return (top, sub) if declared else (None, None)


def fetch_horizon():
    if os.path.exists(os.path.join(HORIZON, "Horizon.Server.sln")):
        return
    top, sub = submodule_checkout()
    if top:  # our git checkout: use the pinned submodule
        run(["git", "submodule", "update", "--init", sub], cwd=top)
    else:  # zip download (or unpacked inside some other repo): no submodule to use
        run(["git", "clone", HORIZON_URL, HORIZON])
        run(["git", "checkout", "-q", HORIZON_COMMIT], cwd=HORIZON)


def git_apply_ok(*args):
    return subprocess.run(["git", "apply", "--check", *args, PATCH], cwd=HORIZON, capture_output=True).returncode == 0


def patch_horizon():
    # Upstream stores many files with CRLF endings while this repo's copy of the patch is LF. Git on
    # Windows converts when applying; elsewhere the context lines differ only in the line ending,
    # so fall back to --ignore-whitespace.
    for ws in ([], ["--ignore-whitespace"]):
        if git_apply_ok("--reverse", *ws):
            print("Horizon patch already applied")
            return
        if git_apply_ok(*ws):
            run(["git", "apply", "--whitespace=nowarn", *ws, PATCH], cwd=HORIZON)
            return
    sys.exit(f"docs/horizon-server.patch doesn't apply to {HORIZON}. It needs a clean checkout of "
             f"commit {HORIZON_COMMIT}; reset it with 'git checkout -- . && git clean -fd' there.")


def build_horizon():
    for project, folder in SERVERS:
        run(["dotnet", "build", os.path.join(project, project + ".csproj"), "-c", "Release", "-nologo",
             "-v", "q"], cwd=HORIZON)
        out = os.path.join(HORIZON, project, "bin", "Release", "net10.0")
        dest = os.path.join(ROOT, folder)
        os.makedirs(dest, exist_ok=True)
        shutil.copytree(out, dest, dirs_exist_ok=True)
        print(f"copied {project} -> {folder}/")


def game_data(usrdir):
    if not os.path.isdir(usrdir):
        sys.exit(f"USRDIR not found: {usrdir}")
    server = os.path.join(ROOT, "server")
    for script in ("tools/extract_appdata.py", "tools/extract_gamedata.py"):
        run([sys.executable, script, usrdir], cwd=server)
    try:  # the admin/public page images are optional and need Pillow
        import PIL  # noqa: F401
        run([sys.executable, "tools/extract_admin_images.py", usrdir], cwd=server)
    except ImportError:
        print("Pillow is not installed, so the admin page images were skipped. Install it with "
              "'pip install pillow', then run: python build.py --skip-horizon --usrdir <USRDIR>")
    if not os.path.exists(os.path.join(server, "appdata", "lobby.csv")):  # keep an existing layout
        run([sys.executable, "-c", "import settings; settings.write_lobby_csvs()"], cwd=server)
    run([sys.executable, "make_appdata.py"], cwd=server)
    run([sys.executable, "make_ssinfo.py"], cwd=server)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--usrdir", help="installed game folder (NPUA80864/USRDIR) to extract data from")
    ap.add_argument("--skip-horizon", action="store_true", help="don't build the Medius servers")
    a = ap.parse_args()
    if not a.skip_horizon:
        need("git", "Install Git: https://git-scm.com/")
        need("dotnet", "Install the .NET 10 SDK: https://dotnet.microsoft.com/download")
        fetch_horizon()
        patch_horizon()
        build_horizon()
    if a.usrdir:
        game_data(a.usrdir)
    else:
        print("\nGame data not generated: run again with --usrdir <NPUA80864/USRDIR> (see README).")
    start = "start_background.bat (or start_all.bat)" if os.name == "nt" else "./start.sh (or the systemd unit in linux/)"
    print(f"\nDone. Start the servers with {start}.")


if __name__ == "__main__":
    main()
