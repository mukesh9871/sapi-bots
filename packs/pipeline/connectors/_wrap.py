"""Runs one pipeline item and records how it ended (Milestone 14, P-PIPE-6).

Started by the hub as its own detached process: `python -I _wrap.py <run folder>`. It reads
spec.json (the program and its arguments as a list, the folder, extra environment), runs it with
no shell and no window, sends everything it prints to output.log, then writes result.json with the
exit code. Because it is not the hub, a run goes on when the hub restarts or closes.

Windows (review M14): the program runs inside a job that ends every process in it when this wrapper
is ended (Stop, time limit), also a renderer whose launcher already exited. A venv or py launcher and
a Microsoft Store Python move their children out of it; the hub's taskkill /T covers those. When the program ends by
itself, that rule is lifted first, so anything it left running on purpose (a server) is left alone.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

KILL_ON_CLOSE, BREAKAWAY_OK = 0x2000, 0x800  # a process started with "break away" may still leave the job
NO_WINDOW, SUSPENDED = 0x08000000, 0x4


class _Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _Extended(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _set_limits(k32, job, flags: int) -> bool:
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = flags
    return bool(k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)))  # 9: extended


def _job():
    """A job that ends its processes when its last handle (this wrapper's) closes. None if not possible."""
    if os.name != "nt":
        return None, None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    job = k32.CreateJobObjectW(None, None)
    if not job or not _set_limits(k32, job, KILL_ON_CLOSE | BREAKAWAY_OK):
        return None, None
    return k32, job


def _write(path: Path, info: dict) -> None:
    """All or nothing (the hub never reads half a file). The hub reads these files every few seconds,
    and Windows refuses to replace a file while it is open: try again for up to a minute (review M14)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(info), encoding="utf-8")
    for _ in range(600):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1)
    os.replace(tmp, path)


def _my_start():
    """This process's start time as Windows counts it (the same number the hub reads), or None."""
    if os.name != "nt":
        return 0
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    times = [ctypes.c_ulonglong() for _ in range(4)]
    ok = k32.GetProcessTimes(ctypes.c_void_p(k32.GetCurrentProcess()), *(ctypes.byref(t) for t in times))
    return times[0].value if ok else None


def _say_pid(run: Path, info: dict) -> None:
    try:
        _write(run / "pid.json", info)
    except OSError:
        pass  # only a hint for the hub (it can also watch the process it started): never a reason to fail


def main() -> int:
    run = Path(sys.argv[1])
    # First thing: this process's own id. A venv python.exe is only a launcher that starts the real
    # Python as a child, so the id the hub got from starting it may not be this process (review M14).
    me = {"wrapper": os.getpid(), "created": _my_start()}  # its start time: the hub never mistakes it
    _say_pid(run, me)
    spec = json.loads((run / "spec.json").read_text(encoding="utf-8"))
    env = {**os.environ, **spec.get("env", {})}
    code = -1
    k32, job = _job()
    in_job = False
    with open(run / "output.log", "ab") as log:
        try:
            flags = (NO_WINDOW | (SUSPENDED if job else 0)) if os.name == "nt" else 0
            proc = subprocess.Popen(spec["argv"], cwd=spec["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, creationflags=flags)
            if job:  # in the job before it runs a single instruction, so nothing it starts escapes
                handle = ctypes.c_void_p(int(proc._handle))
                in_job = bool(k32.AssignProcessToJobObject(job, handle))
                ntdll = ctypes.WinDLL("ntdll")
                ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
                ntdll.NtResumeProcess(handle)
        except OSError as exc:  # the program could not start at all
            log.write(f"\n[pipeline] could not start the program: {exc}\n".encode())
            proc = None
        if proc is not None:  # started: nothing below may turn that into "could not start" (review M14)
            _say_pid(run, {**me, "child": proc.pid, "job": in_job})
            code = proc.wait()
    if job:
        _set_limits(k32, job, 0)  # ended by itself: what it left running on purpose stays
    _write(run / "result.json", {"code": code, "ended": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
