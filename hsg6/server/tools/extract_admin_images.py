"""Extract artwork for the admin page from an installed copy of the game into admin/img/.

Usage: python tools/extract_admin_images.py [USRDIR]      (needs Pillow: pip install pillow)
Source: xbdata/menu/online_menu/choose_lobby/choose_lobby.US_01.xb (lobby-select screen):
  choose_lobby_bg  -> bg.jpg            sky / cloud background
  choose_lobby     -> icon_clubhouse/floor/lobby.png  (atlas regions layer_icon001-003)
  clb_000..002     -> club_N.jpg        clubhouse thumbnails (lion, flowers, golf)
  flr_000..023     -> floor_N.jpg       floor thumbnails (club*10 + floor)
  lby_000..006     -> lobby_N.jpg       lobby map thumbnails (map 8 + N)
Textures are DDS (DXT1/5) stored at a higher resolution than the nominal size in their .map file,
so crop rectangles are scaled by actual/nominal.
"""
import io, os, struct, sys, zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from fbarc import fbhash  # noqa: E402
from PIL import Image  # noqa: E402

P = "data/menu/online_menu/choose_lobby/"


def fb_read(path):
    d = open(path, "rb").read()
    cnt, ns = struct.unpack_from("<II", d, 4)
    base = 12 + (ns & ~3)
    ents = [struct.unpack_from("<5I", d, base + i * 20) for i in range(cnt)]
    off, blocks = base + cnt * 20, []
    for e in ents:
        b = d[off:off + e[3]]
        off += (e[3] + 3) & ~3
        blocks.append(zlib.decompress(b) if e[4] else b)
    return {e[0]: blocks[e[2]] for e in ents}


def tex(files, name):
    """(PIL image, {region: (x0,y0,x1,y1)} in image pixels)."""
    img = Image.open(io.BytesIO(files[fbhash(P + name + ".DDS")])).convert("RGBA")
    lines = files[fbhash(P + name + ".map")].decode("latin1").splitlines()
    _, nw, nh, _ = lines[1].split(",")
    sx, sy = img.width / int(nw), img.height / int(nh)
    regions = {}
    for l in lines[2:]:
        f = l.split(",")
        if len(f) == 5 and "@" in f[0]:
            x0, y0, x1, y1 = map(int, f[1:])
            regions[f[0].split("@")[1]] = (round(x0 * sx), round(y0 * sy), round((x1 + 1) * sx), round((y1 + 1) * sy))
    return img, regions


def main(usr):
    out = os.path.join(HERE, "admin", "img")
    os.makedirs(out, exist_ok=True)
    files = fb_read(os.path.join(usr, "xbdata", "menu", "online_menu", "choose_lobby", "choose_lobby.US_01.xb"))
    bg, r = tex(files, "choose_lobby_bg")
    bg.crop(r["choose_lobby_bg001"]).convert("RGB").save(os.path.join(out, "bg.jpg"), quality=88)
    atlas, r = tex(files, "choose_lobby")
    for i, n in ((1, "clubhouse"), (2, "floor"), (3, "lobby")):
        atlas.crop(r[f"layer_icon00{i}"]).save(os.path.join(out, f"icon_{n}.png"))
    for prefix, short, count in (("clb", "club", 3), ("flr", "floor", 24), ("lby", "lobby", 7)):
        # Only <prefix>_000 has a .map; the layout reuses it for the others ("flr_001,flr_000").
        ref, r = tex(files, f"{prefix}_000")
        x0, y0, x1, y1 = r[f"{prefix}_000"]
        fx, fy = x1 / ref.width, y1 / ref.height
        for i in range(count):
            img = Image.open(io.BytesIO(files[fbhash(P + f"{prefix}_{i:03d}.DDS")])).convert("RGB")
            img.crop((0, 0, round(img.width * fx), round(img.height * fy))).save(
                os.path.join(out, f"{short}_{i}.jpg"), quality=85)
    print("images written to", out, len(os.listdir(out)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"D:\Games\rpcs3\dev_hdd0\game\NPUA80864\USRDIR")
