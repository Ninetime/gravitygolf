"""Build www/hsg6ps3/real/files/appdata.xb from ./appdata/* (FB archive, see fbarc.py).

Files the game reads from it (0x53e880): round_speed_2.csv, club_house.csv, floor.csv,
lobby.csv, Rank_AutoHandi.txt, online_grade.csv, online_avatar_action.csv, online_item.csv,
Online_etc.csv, DLC_etc.csv, avatar_parts_get.csv (+2 runtime-named files).
"""
import os
from fbarc import pack

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "appdata")
OUT = os.path.join(ROOT, "www", "hsg6ps3", "real", "files", "appdata.xb")

if __name__ == "__main__":
    files = {}
    for n in sorted(os.listdir(SRC)):
        files[n] = open(os.path.join(SRC, n), "rb").read()
    data = pack(files, compress=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "wb").write(data)
    print(OUT, len(data), "files:", list(files))
