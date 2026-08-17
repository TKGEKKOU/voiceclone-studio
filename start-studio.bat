@echo off
cd /d "%~dp0"
C:\Python311\python.exe -m uvicorn app:app --host 127.0.0.1 --port 9090
pause
