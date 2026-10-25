# Changes to third-party code and game files

This lists every change made outside the HSG6 server's own code (`server/`). The server itself is
original code and is described in [Protocol](Protocol.md) and [Operations](Operations.md). Paths
are relative to `hsg6/`.

## 1. Horizon Medius server (`thirdparty/horizon-server`)

- Upstream: https://github.com/Horizon-Private-Server/horizon-server, by Daniel Gerendasy and the
  Horizon contributors
- License: MIT (© 2020 Daniel Gerendasy). The full notice is in
  [`docs/licenses/horizon-server-LICENSE.txt`](../hsg6/docs/licenses/horizon-server-LICENSE.txt) and applies to the
  Horizon-derived parts of the patch and of the json config files.
- Base commit: `b1a0d0398f58b2d2b72ffc122053e788e794185a` ("Merge pull request #38 … dme-improvements")
- Source: git submodule `thirdparty/horizon-server`, pinned to the base commit.
- Full diff: [`docs/horizon-server.patch`](../hsg6/docs/horizon-server.patch) (new files are included). The submodule
  stays at the unmodified upstream commit, and the patch is applied to its working tree at build
  time (`ignore = dirty` in `.gitmodules` keeps that out of `git status`).
- Build: `python build.py` applies the patch, runs `dotnet build <project>.csproj -c Release`
  (.NET SDK 10) and copies `bin/Release/net10.0/*` into `medius/`, `muis/`, `dme/`, `nat/`.
  To do it by hand: `git apply docs/horizon-server.patch` inside the submodule, then the same
  build and copy.
- To move to a newer Horizon: update the submodule, re-create the patch against it (`git diff`
  inside the submodule), and change `HORIZON_COMMIT` in `build.py`.

All game-specific behaviour is keyed on application id **24224** (HSG6), so other titles keep
Horizon's default behaviour.

| File | Change | Why |
|---|---|---|
| `Server.UniverseInformation/MUIS.cs` | Skip `RT_MSG_SERVER_CRYPTKEY_GAME` for PS3 clients and for clients without a session key. Also removes a stray BOM. | HSG6 connects unencrypted and never sends `CRYPTKEY_PUBLIC`, so there is no session key; upstream threw and dropped the connection. |
| `RT.Models/Lobby/MediusGetTotalUsersRequest.cs` / `…Response.cs` (new) | Lobby message 0xA1/0xA2 `GetTotalUsers`. | HSG6 asks for it on the lobby-select screen ("Players" box). Upstream had no model, so the request was "Unhandled". |
| `RT.Models/Lobby/MediusGameListRequest.cs` / `…Response.cs` (new) | Old-style lobby message 0x29/0x2A `GameList`. | HSG6 polls it on the lobby-select screen. |
| `Server.Medius/Medius/MLS.cs` | Handlers for `GetTotalUsers` and `GameList`. `TextFilterRequest1` replies echo the text for app 24224. | See above; chat (below). |
| `RT.Models/Lobby/MediusJoinGameResponse.cs` | New `OmitTrailer` flag: skip the 4-byte trailer Horizon adds when `MediusVersion >= 112`. | HSG6 reports Medius version 113 but its SCE-RT 3.02 decoder expects the 0xBC-byte struct without the trailer. With the trailer the client aborts the lobby connection (disconnect reason 4) right after joining. |
| `Server.Medius/Medius/MPS.cs` | Set `OmitTrailer` for app 24224. If the DME's public key is all zero, send `GlobalAuthPublic` as the server key. | Join fix (above); the unencrypted DME returns a zero key. Connection type stays `ClientServerTCPAuxUDP`, the only connect message the DME handles. |
| `RT.Models/Lobby/MediusTextFilterRequest1.cs` | Keep the sent text length (`TextLength`). | Needed to echo the text exactly. |
| `RT.Models/Lobby/MediusTextFilterResponse1.cs` | Optional `Text`. When set, write `status, int length, text[length]` with **no** padding after the message id. | The SCE-RT 3.02 decoder for 0x76 reads the status straight after the 21-byte message id. Horizon's 3 padding bytes shifted every field, the reply failed to decode, and the game disconnected after every chat line. |
| `Server.Medius/Medius/MediusManager.cs` | `GetAllClients()`, `GetAllGames()`. | Status API. |
| `Server.Medius/StatusApi.cs` (new), `Program.cs`, `Config/ServerSettings.cs` | Read-only JSON endpoint `GET http://127.0.0.1:<StatusPort>/status` (default 10081; `"StatusPort": 0` disables it). Lists clients and games. | Used by the admin page. Plain `TcpListener`, so no URL ACL or admin rights are needed. |
| `Server.NAT/Program.cs` | `await Task.Delay(-1)` after `Start()`. | Upstream `Main` returned as soon as the UDP socket was bound, so the NAT server exited immediately. |

Configuration files (tracked in git as templates) live next to each binary: `muis/muis.json`, `medius/medius.json`,
`dme/dme.json`, `nat/nat.json`. See [Operations](Operations.md#4-configuration-files).

## 2. RPCS3 game patch ("Server patching")

The game patch is published in RPCS3's patch database, so it appears in RPCS3's Patch Manager. It
redirects the game's bootstrap URL from `https://cmnap.scej-online.jp:18443/hsg6ps3/ss.info` to
plain HTTP port 18000. The current patch entry is on the [RPCS3 Patch](RPCS3-Patch.md) page.

## 3. RPCS3 per-game configuration

`config/custom_configs/config_NPUA80864.yml` → Network → `IP swap list: cmnap.scej-online.jp=127.0.0.1`
(replace `127.0.0.1` with the server's address for remote players). A backup of the original is at
`config_NPUA80864.yml.bak-hsg6`.

## 4. Ghidra (analysis only)

`ppc_64_32.cspec` in Ghidra 12.1.2: `r2` added to `<unaffected>` (backup `.bak`) so TOC-relative
loads decompile to constants. This only affects analysis output, not the server.

## 5. Known RPCS3 limitation (not changed)

**Friends List softlock in the lobby** (Online Menu → Communicate → Friends List): the game calls
`sceNpFriendlist(resultHandler, …)` and waits for the result handler. RPCS3's implementation
(`rpcs3/Emu/Cell/Modules/sceNp.cpp`, `sceNpFriendlist`) returns `CELL_OK` with `// TODO: handler`.
It never shows a UI and never calls the handler, so the game waits forever. The fix belongs in
RPCS3: show the friend list or at least call the handler with an "aborted" result asynchronously,
as was done for `sceNpBasicRecvMessageCustom` in the rpcs3-nine fork.
