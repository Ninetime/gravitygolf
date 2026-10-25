@echo off
rem Stops every HSG6 server process started by start_background.bat.
curl -s -X POST http://127.0.0.1:18081/shutdown >nul && echo HSG6 stopped. || echo Supervisor not running.
