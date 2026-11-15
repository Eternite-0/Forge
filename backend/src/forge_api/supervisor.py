"""Independent process supervision for the default local Core launcher.

This process owns no Agent state and never replays an action. A replacement Core
recovers through the same database, Workspace lock, leases and receipt checks.
The native desktop already owns its child's lifecycle and retains its protocol.
"""

import argparse
import hashlib
import math
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from forge_core.settings import Workspace, default_workspace_root
from forge_core.workspace_backup import FENCE_NAME
from forge_core.workspace_lock import WorkspaceBusy, WorkspaceLock, workspace_key

from .desktop_host import write_frame


def validate_secret(secret):
    try:
        raw = bytes.fromhex(secret)
    except (TypeError, ValueError):
        raise ValueError("Core bootstrap secret is unavailable") from None
    if len(secret) != 64 or len(raw) != 32 or len(set(raw)) < 12:
        raise ValueError("Core bootstrap secret is unavailable")
    return secret


class _SupervisorLock(WorkspaceLock):
    """Reuse the proven exclusive-lock primitive with a separate ownership key."""

    def __init__(self, root):
        super().__init__(root)
        digest = hashlib.sha256(workspace_key(root).encode("utf-8")).hexdigest()[:24]
        self.path = Path(root).parent / f".forge-supervisor-{digest}.lock"


class _WindowsJob:
    """Kill the Core and its descendants even if this parent is terminated."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class BasicAccounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._accounting = BasicAccounting
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Core process supervision is unavailable")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise OSError("Core process supervision is unavailable")

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("Core process supervision is unavailable")

    def terminate(self):
        import ctypes

        if not self.handle:
            return
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise OSError("Core process cleanup failed")
        deadline = time.monotonic() + 5
        while True:
            accounting = self._accounting()
            if not self.kernel.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
            ):
                raise OSError("Core process cleanup failed")
            if accounting.ActiveProcesses == 0:
                return
            if time.monotonic() >= deadline:
                raise OSError("Core process cleanup timed out")
            time.sleep(0.01)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _kill_tree(process, job):
    if job is not None:
        job.terminate()
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()


def _stop_child(process, job, timeout):
    try:
        if process.poll() is None:
            try:
                write_frame(process.stdin, {"version": 1, "type": "shutdown"})
            except (BrokenPipeError, OSError, ValueError):
                pass
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(process, job)
                process.wait(timeout=5)
        # A tool may have outlived its Core. Drain the old process tree before a
        # new owner can start, irrespective of the Core's own return code.
        _kill_tree(process, job)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if job is not None:
            job.close()


def supervise(
    root,
    *,
    command=None,
    secret=None,
    max_restarts=5,
    initial_backoff=1,
    max_backoff=16,
    stable_seconds=300,
    window_seconds=600,
    shutdown_timeout=10,
    stop_event=None,
):
    """Run one Core at a time, with bounded restart rate and no state mutation.

    Injectable timing/command values support deterministic real-process tests;
    the CLI deliberately exposes no arbitrary command execution setting.
    """
    root = Workspace.from_path(root).root
    if (
        type(max_restarts) is not int
        or not 0 <= max_restarts <= 100
        or any(
            type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            for value in (
                initial_backoff,
                max_backoff,
                stable_seconds,
                window_seconds,
                shutdown_timeout,
            )
        )
    ):
        raise ValueError("Invalid Core supervision limits")
    try:
        secret = validate_secret(
            secret if secret is not None else os.environ.get("FORGE_CORE_SECRET")
        )
    except ValueError:
        print(
            "FORGE Core requires a random bootstrap secret in the process environment.",
            file=sys.stderr,
        )
        return 2
    stop = stop_event if stop_event is not None else threading.Event()
    handlers = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda *_: stop.set())
    environment = dict(os.environ)
    environment.pop("FORGE_CORE_SECRET", None)
    environment["FORGE_WORKSPACE_ROOT"] = str(root)
    child_command = command or [
        sys.executable,
        "-m",
        "forge_api",
        "--core-child",
        "--workspace-root",
        str(root),
    ]
    restarts = []
    try:
        with _SupervisorLock(root):
            while not stop.is_set():
                job = _WindowsJob() if os.name == "nt" else None
                process = None
                started = time.monotonic()
                try:
                    process = subprocess.Popen(
                        child_command,
                        env=environment,
                        stdin=subprocess.PIPE,
                        shell=False,
                        close_fds=True,
                        **(
                            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                            if os.name == "nt"
                            else {"start_new_session": True}
                        ),
                    )
                    if job is not None:
                        job.assign(process)
                    try:
                        write_frame(
                            process.stdin, {"version": 1, "type": "bootstrap", "secret": secret}
                        )
                    except (BrokenPipeError, OSError):
                        # An immediate, normal child exit remains a normal exit.
                        process.wait(timeout=5)
                    while process.poll() is None and not stop.wait(0.1):
                        pass
                    if stop.is_set():
                        return 0
                    code = process.returncode
                finally:
                    if process is not None:
                        _stop_child(process, job, shutdown_timeout)
                    elif job is not None:
                        job.close()
                if code in (0, 2, 3):
                    return code
                if (root / FENCE_NAME).exists():
                    print(
                        "FORGE Core recovery stopped: workspace restore review is required.",
                        file=sys.stderr,
                    )
                    return 3
                now = time.monotonic()
                restarts = (
                    []
                    if now - started >= stable_seconds
                    else [previous for previous in restarts if now - previous < window_seconds]
                )
                if len(restarts) >= max_restarts:
                    print(
                        "FORGE Core recovery stopped after repeated process failures.",
                        file=sys.stderr,
                    )
                    return 1
                restarts.append(now)
                delay = min(initial_backoff * 2 ** (len(restarts) - 1), max_backoff)
                print(
                    f"FORGE Core exited unexpectedly; restarting ({len(restarts)}/{max_restarts}) in {delay:g}s.",
                    file=sys.stderr,
                )
                if stop.wait(delay):
                    return 0
                # A restore can begin during backoff, after the child released its
                # WorkspaceLock. Never start a replacement across that boundary.
                if (root / FENCE_NAME).exists():
                    print(
                        "FORGE Core recovery stopped: workspace restore review is required.",
                        file=sys.stderr,
                    )
                    return 3
            return 0
    except WorkspaceBusy:
        print("FORGE Core supervisor is already running for this workspace.", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError):
        print("FORGE Core process supervision failed.", file=sys.stderr)
        return 2
    finally:
        for signum, previous in handlers.items():
            signal.signal(signum, previous)


def main():
    parser = argparse.ArgumentParser(description="Supervise the local FORGE Core process")
    parser.add_argument("--workspace-root", help="Absolute Workspace root")
    args = parser.parse_args()
    root = args.workspace_root or os.environ.get(
        "FORGE_WORKSPACE_ROOT", str(default_workspace_root())
    )
    try:
        result = supervise(root)
    except ValueError:
        parser.error("Invalid Workspace or process supervision configuration")
    raise SystemExit(result)


if __name__ == "__main__":
    main()
