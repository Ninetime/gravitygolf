#!/bin/sh
# Starts the HSG6 supervisor in the background (Linux). It starts every server process (HTTP, MUIS,
# Medius, DME, NAT), logs to logs/<name>.log and restarts crashed processes.
# For a permanent install use the systemd unit in linux/hsg6.service instead.
# Optional: set HSG6_DAY_OFFSET / HSG6_ADMIN_BIND / HSG6_PUBLIC_PORT before running.
cd "$(dirname "$0")" || exit 1
command -v dotnet >/dev/null 2>&1 || { echo "dotnet not found on PATH (install the .NET 10 runtime)"; exit 1; }
mkdir -p logs
nohup python3 -u supervisor.py >> logs/supervisor.out 2>&1 &
echo "HSG6 started in the background. Admin page: http://127.0.0.1:18080/  Public page: http://127.0.0.1:18082/"
