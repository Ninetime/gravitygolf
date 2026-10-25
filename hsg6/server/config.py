"""Shared settings for the HSG6 replacement servers."""
import os
# Host name the game uses for HTTP; RPCS3 "IP swap list" maps it to the server IP.
HTTP_HOST = os.environ.get("HSG6_HTTP_HOST", "cmnap.scej-online.jp")
HTTP_PORT = int(os.environ.get("HSG6_PORT", "18000"))
# Medius authentication server (Horizon MAS) address handed to the game via ss.info.
MEDIUS_HOST = os.environ.get("HSG6_MEDIUS_HOST", "127.0.0.1")
MEDIUS_PORT = int(os.environ.get("HSG6_MEDIUS_PORT", "10075"))
BASE = f"http://{HTTP_HOST}:{HTTP_PORT}/hsg6ps3"
# Testing aid: shift the server clock by this many days (e.g. 1 to end today's Daily tournament).
DAY_OFFSET = int(os.environ.get("HSG6_DAY_OFFSET", "0"))
