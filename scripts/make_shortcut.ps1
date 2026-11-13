# Creates the "AI Hub" desktop shortcut (Milestone 4: one-click launch).
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\make_shortcut.ps1
# - Points at start.bat in this folder, so it keeps working after a git pull.
# - The console starts minimized: it sits in the taskbar, close it to stop the app.
# - Uses only Windows' built-in WScript.Shell, no extra dependency.

param([string]$Folder = "")                         # tests/test_launch.py passes a temp folder

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot           # the app folder (scripts\..)
$target = Join-Path $root "start.bat"
if (-not (Test-Path $target)) { throw "start.bat not found in $root" }

# GetFolderPath follows a Desktop moved to OneDrive, unlike $HOME\Desktop.
$desktop = if ($Folder) { $Folder } else { [Environment]::GetFolderPath("Desktop") }
$link = Join-Path $desktop "AI Hub.lnk"

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $target
$sc.WorkingDirectory = $root
$sc.WindowStyle = 7                                # 7 = minimized
$sc.Description = "Start the personal AI hub and open its chat window"
$sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$sc.Save()

Write-Host "Shortcut created: $link"
