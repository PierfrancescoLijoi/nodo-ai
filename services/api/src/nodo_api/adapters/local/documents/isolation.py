"""Run a short-lived Python process for untrusted input.

The child gets an allowlisted environment (no engine secrets), a memory ceiling, a hard time
limit that kills the whole process tree, no stdin, a discarded stderr and a bounded stdout. Nothing
in this module imports a parser library.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from typing import Any

from nodo_api.domain.documents import ParseError

MAX_OUTPUT_BYTES = 64 * 1024 * 1024

# What a Python interpreter needs to start on Windows and POSIX. PATH, HOME, APPDATA and every
# NODO_* secret stay behind.
_ENV_ALLOWLIST = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)
# Forwarded only when the engine itself runs as the test environment.
_TEST_HOOKS = (
    "NODO_ENVIRONMENT",
    "NODO_DOCUMENT_WORKER_TEST_HOOK",
    "NODO_DOCUMENT_WORKER_TEST_FILE",
)


def child_environment(memory_mb: int) -> dict[str, str]:
    environment = {name: os.environ[name] for name in _ENV_ALLOWLIST if name in os.environ}
    if os.environ.get("NODO_ENVIRONMENT") == "test":
        environment.update({name: os.environ[name] for name in _TEST_HOOKS if name in os.environ})
    environment["NODO_DOCUMENT_MEMORY_MB"] = str(memory_mb)
    return environment


def _windows_memory_job(
    process: subprocess.Popen[bytes], memory_bytes: int
) -> tuple[Any, int] | None:
    """Put a Windows worker in a kill-on-close Job with a per-process memory ceiling."""
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    class _BasicLimit(ctypes.Structure):
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

    class _IoCounters(ctypes.Structure):
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

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "could not create parser memory job")
    limits = _ExtendedLimit()
    # JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    limits.BasicLimitInformation.LimitFlags = 0x100 | 0x2000
    limits.ProcessMemoryLimit = memory_bytes
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "could not set parser memory limit")
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "could not assign parser memory job")
    return kernel32, int(job)


def _close_job(job: tuple[Any, int] | None) -> None:
    if job is not None:
        kernel32, handle = job
        kernel32.CloseHandle(handle)  # kill-on-close ends every process still in the Job


def _kill_tree(process: subprocess.Popen[bytes], job: tuple[Any, int] | None) -> None:
    if os.name != "nt":
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()
    _close_job(job)


def run_isolated(
    args: Sequence[str],
    *,
    timeout: float,
    memory_mb: int,
    max_output: int = MAX_OUTPUT_BYTES,
) -> tuple[int, bytes]:
    """Run `python -I <args>` and return its exit code and bounded stdout.

    Raises ParseError("timeout") when the limit passes (the process tree is killed first) and
    ParseError("damaged") when the child cannot be confined or writes more than `max_output`.
    """
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True  # one process group to kill
    process = subprocess.Popen(
        [sys.executable, "-I", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=child_environment(memory_mb),
        **options,
    )
    try:
        job = _windows_memory_job(process, memory_mb * 1024 * 1024)
    except OSError:
        _kill_tree(process, None)
        process.wait()
        process.stdout.close()  # type: ignore[union-attr]
        raise ParseError("damaged") from None

    chunks: list[bytes] = []
    state = {"size": 0, "over": False}

    def read() -> None:
        stream = process.stdout
        while chunk := stream.read1(65536):  # type: ignore[union-attr]
            state["size"] += len(chunk)
            if state["size"] > max_output:
                state["over"] = True
                return
            chunks.append(chunk)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        reader.join(timeout)
        if reader.is_alive():
            timed_out = True
        elif not state["over"]:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        if timed_out or state["over"] or process.poll() is None:
            _kill_tree(process, job)
        else:
            _close_job(job)
        reader.join(10)
        process.wait()
        process.stdout.close()  # type: ignore[union-attr]
    if timed_out:
        raise ParseError("timeout")
    if state["over"]:
        raise ParseError("damaged")
    return process.returncode, b"".join(chunks)
