"""Real Python tests from an exact approved snapshot, with process-tree cleanup.

This is deliberately an R2 tool. A private directory and a clean environment
are not an OS sandbox and do not contain arbitrary Python's filesystem/network
effects. The Core owns argv; the caller selects only a bounded file snapshot.
"""

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath

MAX_BYTES = 262144
MAX_FILES = 32
MAX_SECONDS = 10


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_manifest(manifest, test_module):
    if type(manifest) is not dict or not 1 <= len(manifest) <= MAX_FILES:
        raise ValueError("Python test manifest must contain 1-32 files")
    normalized = set()
    for name, digest in manifest.items():
        if (
            type(name) is not str
            or len(name) > 240
            or "\\" in name
            or str(PurePosixPath(name)) != name
            or not re.fullmatch(
                r"(?:[A-Za-z][A-Za-z0-9_]*/)*(?:[A-Za-z][A-Za-z0-9_]*|__init__)\.py", name
            )
            or type(digest) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or name.casefold() in normalized
            or any(
                re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                for part in PurePosixPath(name).parts
            )
        ):
            raise ValueError("Invalid Python test manifest entry")
        normalized.add(name.casefold())
    if (
        type(test_module) is not str
        or len(test_module) > 200
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*", test_module)
        or test_module.replace(".", "/") + ".py" not in manifest
    ):
        raise ValueError("Test module must identify a declared Python file")


def read_snapshot(directory, manifest, test_module):
    """Read each exact declared regular file; reject links and stale approvals."""
    validate_manifest(manifest, test_module)
    directory = Path(directory)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or directory.resolve() != directory
    ):
        raise ValueError("Python test source must be an ordinary directory")
    snapshot = {}
    total = 0
    for name, expected in sorted(manifest.items()):
        target = directory
        for part in PurePosixPath(name).parts:
            target = target / part
            info = target.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or target.resolve() != target
            ):
                raise ValueError("Python test snapshot cannot contain links")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Python test snapshot requires regular files")
        with target.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        total += len(data)
        if total > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Python test source differs from the approved manifest")
        data.decode("utf-8")
        snapshot[name] = data
    return snapshot


class _WindowsExecutionJob:
    """Kill the approved interpreter and its descendants on close."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
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

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
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
        self.kernel, self.accounting = kernel, Accounting
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Python process-tree supervision unavailable")
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        if not kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise OSError("Python process-tree supervision unavailable")

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("Python process-tree supervision unavailable")

    def terminate(self):
        import ctypes

        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise OSError("Python process-tree cleanup failed")
        deadline = time.monotonic() + 5
        while True:
            info = self.accounting()
            if not self.kernel.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
            ):
                raise OSError("Python process-tree cleanup failed")
            if not info.ActiveProcesses:
                return
            if time.monotonic() >= deadline:
                raise OSError("Python process-tree cleanup timed out")
            time.sleep(0.01)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run_snapshot(snapshot, test_module, *, cwd, env, cancelled=None):
    """Execute after private snapshot creation; never inherit Provider secrets."""
    directory = Path(cwd)
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in snapshot.items()}
    validate_manifest(manifest, test_module)
    for name, data in snapshot.items():
        target = directory / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
    argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve())]
    payload = canonical({"root": str(directory), "test_module": test_module}).encode("utf-8")
    job = _WindowsExecutionJob() if os.name == "nt" else None
    process = None
    buffers = [bytearray(), bytearray()]
    output_exceeded = threading.Event()
    threads = []

    def stop_tree():
        if job is not None:
            job.terminate()
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def read_pipe(pipe, output):
        while chunk := pipe.read(8192):
            output.extend(chunk)
            if sum(map(len, buffers)) > MAX_BYTES:
                output_exceeded.set()
                return

    try:
        process = subprocess.Popen(
            argv,
            cwd=str(directory),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            **(
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt"
                else {"start_new_session": True}
            ),
        )
        # The harness reads stdin before importing candidate code. Assign its Job
        # first, so even an immediate candidate subprocess belongs to this tree.
        if job is not None:
            job.assign(process)
        for pipe, output in zip((process.stdout, process.stderr), buffers):
            thread = threading.Thread(target=read_pipe, args=(pipe, output), daemon=True)
            thread.start()
            threads.append(thread)
        process.stdin.write(payload)
        process.stdin.close()
        deadline = time.monotonic() + MAX_SECONDS
        while process.poll() is None:
            if output_exceeded.is_set() or (cancelled is not None and cancelled.is_set()):
                raise RuntimeError("Python test execution stopped; external effects may be unknown")
            if time.monotonic() >= deadline:
                raise RuntimeError("Python test deadline exceeded; external effects may be unknown")
            time.sleep(0.02)
        # Kill descendants even after the interpreter exits normally, before
        # reading a final result or allowing another owner to execute work.
        stop_tree()
        for thread in threads:
            thread.join(timeout=2)
        if output_exceeded.is_set() or any(thread.is_alive() for thread in threads):
            raise RuntimeError("Python test output exceeds limit")
        stdout, stderr = map(bytes, buffers)
        report = json.loads(stdout)
        if (
            type(report) is not dict
            or set(report) != {"tests_run", "failures", "errors", "skipped", "successful"}
            or any(
                type(report[key]) is not int or not 0 <= report[key] <= 10000
                for key in ("tests_run", "failures", "errors", "skipped")
            )
            or type(report["successful"]) is not bool
            or report["tests_run"] < 1
            or report["successful"] != (report["failures"] == report["errors"] == 0)
            or process.returncode != (0 if report["successful"] else 2)
        ):
            raise ValueError("Invalid Python unittest result")
        return {
            "argv": argv,
            "cwd": str(directory),
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "manifest_sha256": hashlib.sha256(canonical(manifest).encode()).hexdigest(),
            "manifest_json": canonical(manifest),
            "test_module": test_module,
            **{key: report[key] for key in ("tests_run", "failures", "errors", "skipped")},
            "result_json": canonical(report),
            "runtime": "cpython_unittest_v1",
        }
    finally:
        try:
            if process is not None:
                stop_tree()
                process.wait(timeout=5)
        finally:
            if job is not None:
                job.close()
            if process is not None:
                for thread in threads:
                    thread.join(timeout=2)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None and not pipe.closed:
                        pipe.close()


def _main():
    import contextlib
    import unittest

    payload = json.loads(sys.stdin.buffer.read(MAX_BYTES + 1))
    if type(payload) is not dict or set(payload) != {"root", "test_module"}:
        raise ValueError("Invalid test bootstrap")
    sys.path.insert(0, payload["root"])
    # Candidate prints are diagnostic stderr; stdout is the bounded protocol.
    with contextlib.redirect_stdout(sys.stderr):
        suite = unittest.defaultTestLoader.loadTestsFromName(payload["test_module"])
        result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    sys.stdout.write(
        canonical(
            {
                "tests_run": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skipped": len(result.skipped),
                "successful": result.wasSuccessful(),
            }
        )
    )
    sys.stdout.flush()
    return 0 if result.wasSuccessful() else 2


if __name__ == "__main__":
    raise SystemExit(_main())
