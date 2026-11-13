"""M4 check 3: one-click launch. The shortcut script makes a launcher (start.bat, minimized console),
and a second launch says the hub is already running. Shortcut tests use a temp folder, never the real
Desktop. Windows only. No network."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcut")


def ps(command: str) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_shortcut_points_at_start_bat_minimized(tmp_path):
    script = ROOT / "scripts" / "make_shortcut.ps1"
    ps(f"& '{script}' -Folder '{tmp_path}'")
    link = tmp_path / "AI Hub.lnk"
    assert link.is_file()
    info = ps(f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); "
              "\"$($s.TargetPath)|$($s.WorkingDirectory)|$($s.WindowStyle)\"")
    target, workdir, style = info.split("|")
    assert Path(target) == ROOT / "start.bat"
    assert Path(workdir) == ROOT  # start.bat finds .venv and hub from here
    assert style == "7"  # minimized console


# ---- second launch while the hub runs (review M4: the console starts minimized) ----------

def test_second_launch_says_already_running(monkeypatch):
    import socket

    from hub import config, server

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert server.port_in_use(port)
        monkeypatch.setattr(config, "load", lambda: {**config.DEFAULTS, "port": port})
        monkeypatch.setattr(server, "scrub_billing_env", lambda: [])  # leave this process's env alone
        assert server.main() == server.EXIT_ALREADY_RUNNING  # start.bat shows a message box for 2
    assert not server.port_in_use(port)


def test_start_bat_shows_a_message_when_start_fails():
    bat = (ROOT / "start.bat").read_text(encoding="utf-8")
    assert '"%code%"=="2"' in bat and "already running" in bat
    assert 'not "%code%"=="0"' in bat and "MessageBox" in bat
