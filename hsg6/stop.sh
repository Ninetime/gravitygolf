#!/bin/sh
# Stops every HSG6 server process started by start.sh.
python3 -c 'import urllib.request; urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18081/shutdown", method="POST"), timeout=5)' 2>/dev/null \
  && echo "HSG6 stopped." || echo "Supervisor not running."
