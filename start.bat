@echo off
rem Starts the hub and opens the chat window in your browser. Close this window to stop it.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m hub.server
set code=%errorlevel%
rem The desktop shortcut starts this window minimized, so problems are also shown in a
rem message box (built-in Windows parts only). 2 = the hub is already running.
if "%code%"=="2" (
  powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('AI Hub is probably already running: switch to its browser tab, or close the AI Hub console window in the taskbar and start it again. If it is not running, another program is using its port (change port in config.json).', 'AI Hub')"
  exit /b 2
)
if not "%code%"=="0" (
  powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('AI Hub could not start. The reason is in its console window in the taskbar.', 'AI Hub')"
)
pause
