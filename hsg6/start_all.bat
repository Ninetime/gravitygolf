@echo off
rem Starts every HSG6 server process in its own window, in dependency order.
rem Optional: set HSG6_DAY_OFFSET / HSG6_ADMIN_BIND / HSG6_PUBLIC_PORT before running.
cd /d "%~dp0"
start "HSG6 HTTP + admin" /d server cmd /k python -u hsg6_http.py
start "HSG6 MUIS"   /d muis   cmd /k Server.UniverseInformation.exe
start "HSG6 Medius" /d medius cmd /k Server.Medius.exe
timeout /t 5 /nobreak >nul
start "HSG6 DME"    /d dme    cmd /k Server.Dme.exe
start "HSG6 NAT"    /d nat    cmd /k Server.NAT.exe
echo Started. Admin page: http://127.0.0.1:18080/  Public status page: http://127.0.0.1:18082/
