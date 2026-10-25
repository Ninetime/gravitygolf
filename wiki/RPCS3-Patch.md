# RPCS3 patch

The game patch is part of RPCS3's patch database, so you don't need to add it by hand. In RPCS3,
open **Manage → Game Patches** and enable **Server patching** under *Everybody's Golf 6 / Hot
Shots Golf: World Invitational*. If it isn't listed, click **Download latest patches** first.

## What it does

The game asks for its bootstrap file, `ss.info`, at
`https://cmnap.scej-online.jp:18443/hsg6ps3/ss.info`. The patch overwrites that URL in the game
executable with `http://cmnap.scej-online.jp:18000/hsg6ps3/ss.info`, so it uses plain HTTP on
port 18000. This server listens there, which avoids the original TLS setup. Every other address
the game uses comes from `ss.info`.

The host name stays `cmnap.scej-online.jp`. RPCS3's **IP swap list**
(`cmnap.scej-online.jp=<server IP>`) points it at your server, so one patch works for every
server.

## Current entry

This is the entry as published in RPCS3's patch database (checked 2026-10-25). It's here for
reference, or for adding by hand to `patches/NPUA80864_patch.yml` if your RPCS3 can't download
patches:

```yaml
PPU-cdf4e1c581ae975cd6943427043cd527da6937a4:
  "Server patching":
    Games:
      "Everybody's Golf 6 / Hot Shots Golf: World Invitational":
        NPUA80864: [01.03]
    Author: "Ninetime"
    Patch Version: 1.0
    Patch:
      - [utf8, 0x00bfd8c0, "http://cmnap.scej-online.jp:18000/hsg6ps3/ss.info\0"]
```

| field | value |
|---|---|
| Game | NPUA80864, version 01.03 (the patch only applies to that update) |
| PPU hash | `cdf4e1c581ae975cd6943427043cd527da6937a4` |
| Address | `0x00bfd8c0`, the `ss.info` URL string in the executable |
| Author | Ninetime |
| Patch version | 1.0 |

If you add it by hand to a new patch file, start the file with a `Version: 1.2` line, as RPCS3's
own patch files do.
