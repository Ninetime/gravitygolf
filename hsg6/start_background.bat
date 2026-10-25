@echo off
rem Starts the HSG6 supervisor without a window. It starts every server process (HTTP, MUIS,
rem Medius, DME, NAT), logs to logs\<name>.log and restarts crashed processes.
rem Admin page with status + logs: http://127.0.0.1:18080/  (Settings tab)
cd /d "%~dp0"
start "" pythonw "%~dp0supervisor.py"
echo HSG6 started in the background. Admin page: http://127.0.0.1:18080/  Public page: http://127.0.0.1:18082/
