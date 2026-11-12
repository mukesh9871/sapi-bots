@echo off
rem Starts the hub and opens the chat window in your browser. Close this window to stop it.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m hub.server
pause
