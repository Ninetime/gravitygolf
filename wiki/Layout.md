# Layout

| path | contents |
|---|---|
| `hsg6/server/` | Python HTTP servers: `ss.info`, the game API, the "real" API, app data, the admin page and the public page |
| `hsg6/medius/`, `muis/`, `dme/`, `nat/` | config files for the [Horizon](https://github.com/Horizon-Private-Server/horizon-server) Medius servers (lobby, login, game worlds and NAT); `build.py` puts the built servers here |
| `hsg6/thirdparty/horizon-server` | Horizon source (git submodule, pinned commit) |
| `hsg6/build.py` | one-step setup: builds the patched Horizon servers and generates the game data ([Building](Building.md)) |
| `hsg6/supervisor.py`, `*.bat` | start, stop and keep all servers running ([Operations](Operations.md)) |
| `hsg6/docs/` | `horizon-server.patch` (the Horizon changes), `licenses/` (third-party license notices), `images/` (README screenshots) |
| `wiki/` | this documentation |
| `src/`, `Cargo.toml` | an earlier Rust prototype that only logged the game's requests; not needed to run the server |

Files that are generated or come from the game aren't tracked; `hsg6/.gitignore` lists them.
[Building](Building.md) recreates them.
