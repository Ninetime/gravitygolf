"""Regenerate the game-derived files in appdata/ from an installed copy of the game.

Usage: python tools/extract_appdata.py <USRDIR>
  USRDIR = <RPCS3 folder>/dev_hdd0/game/NPUA80864/USRDIR

Writes:
  appdata/Rule_Rule.txt                          rule table (221 rows) from xbdata/chara_localize/chara_param.xb
  appdata/data_US_0{1,2,3}_*_MenuText_ChatText.to easy-chat text (TO1 with 7 category-name tables + phrases)
                                                  from xbdata/menu/message_text.US_0{1,2,3}.xb
  appdata/OnlineData_CommentList_US.txt          easy-chat phrase list referencing those tables
Afterwards run make_appdata.py to rebuild www/hsg6ps3/real/files/appdata.xb.
"""
import os, struct, sys, zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from fbarc import fbhash  # noqa: E402


def fb_read(path):
    """FB archive -> {hash: bytes}. Entry = (hash, name_off, block_index, stored, raw);
    data blocks are stored in entry order and an entry's data is block[block_index]."""
    d = open(path, "rb").read()
    cnt, ns = struct.unpack_from("<II", d, 4)
    base = 12 + (ns & ~3)
    ents = [struct.unpack_from("<5I", d, base + i * 20) for i in range(cnt)]
    off, blocks = base + cnt * 20, []
    for e in ents:
        b = d[off:off + e[3]]
        off += (e[3] + 3) & ~3
        blocks.append(zlib.decompress(b) if e[4] else b)
    return {e[0]: blocks[e[2]] for e in ents}, blocks


def to_counts(b):
    n = struct.unpack_from("<I", b, 8)[0]
    return [struct.unpack_from("<III", b, 12 + t * 12)[2] for t in range(n)]


def main(usr):
    out = os.path.join(HERE, "appdata")
    # 1. Rule_Rule.txt: the full 221-row table (header "Label<TAB>No<TAB>Official...") lives in
    #    chara_param.xb; the entry named data/param/chara/Rule_Rule.txt is only a short stub.
    _, blocks = fb_read(os.path.join(usr, "xbdata", "chara_localize", "chara_param.xb"))
    bom = b"\xef\xbb\xbf"
    rule = max((b for b in blocks if b.lstrip(bom).startswith(b"Label\tNo\tOfficial")), key=len)
    open(os.path.join(out, "Rule_Rule.txt"), "wb").write(rule)

    # 2. Easy-chat text: the TO1 table whose first 7 sub-tables are the category names
    #    (counts 1,3,3,1,3,2,1) followed by the phrase tables.
    for lang, tag in (("US_01", "01_ENG"), ("US_02", "02_FRE"), ("US_03", "03_SPA")):
        _, blocks = fb_read(os.path.join(usr, "xbdata", "menu", f"message_text.{lang}.xb"))
        chat = next(b for b in blocks if b[:4] == b"TO1\0" and to_counts(b)[:7] == [1, 3, 3, 1, 3, 2, 1])
        open(os.path.join(out, f"data_US_{tag}_MenuText_ChatText.to"), "wb").write(chat)
        if lang == "US_01":
            counts = to_counts(chat)

    # 3. Comment list: one row per phrase. Label "Comment<L>_<NNN>": L = category letter
    #    (A..F = phrase tables 7..12, G = room messages, table 14), NNN = phrase index in that table.
    #    Columns 4..11 are flags (all 1 = shown everywhere), column 12 an int (0).
    cats = [("A", counts[7]), ("B", counts[8]), ("C", counts[9]), ("D", counts[10]), ("E", counts[11]),
            ("F", counts[12]), ("G", counts[14])]
    rows = ["Label\tCol1\tCol2\tCol3\tF4\tF5\tF6\tF7\tF8\tF9\tF10\tF11\tVal"]
    for letter, n in cats:
        rows += ["\t".join([f"Comment{letter}_{i:03d}", "", "", ""] + ["1"] * 8 + ["0"]) for i in range(n)]
    open(os.path.join(out, "OnlineData_CommentList_US.txt"), "w", newline="\r\n").write("\n".join(rows) + "\n")
    print("wrote Rule_Rule.txt, 3 ChatText.to,", len(rows) - 1, "comment rows")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
