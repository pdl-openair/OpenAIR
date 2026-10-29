"""Real subprocess execution, bounded stream draining, and process-tree termination."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import BinaryIO

from .execution_common import BoundedCapture

_TERMINATION_TIMEOUT_SECONDS = 10
_STREAM_DRAIN_GRACE_SECONDS = 2.0
_PIPE_POLL_SECONDS = 0.01


def _read_available(stream: BinaryIO, capture: BoundedCapture) -> bool:
    """Read at most one chunk per turn so a busy pipe cannot starve a deadline."""
    try:
        chunk = os.read(stream.fileno(), 65536)
    except BlockingIOError:
        return True
    except (OSError, ValueError):
        capture.mark_incomplete()
        return False
    if not chunk:
        return False
    capture.consume(chunk)
    return True


def _pump(streams: list[tuple[BinaryIO, BoundedCapture]]) -> None:
    streams[:] = [
        (stream, capture) for stream, capture in streams if _read_available(stream, capture)
    ]


def _finish_streams(streams: list[tuple[BinaryIO, BoundedCapture]]) -> None:
    """Both pipes share one grace period; no reader or read lock survives return."""
    deadline = time.monotonic() + _STREAM_DRAIN_GRACE_SECONDS
    while streams:
        _pump(streams)
        if not streams:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(_PIPE_POLL_SECONDS, remaining))
    for _, capture in streams:
        capture.mark_incomplete()


def _wait_with_capture(
    process: subprocess.Popen[bytes],
    streams: list[tuple[BinaryIO, BoundedCapture]],
    timeout_seconds: int,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    while True:
        _pump(streams)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        try:
            return process.wait(timeout=min(_PIPE_POLL_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            # Continue draining while the process runs, within the same deadline.
            continue


def _terminate_windows_tree(process: subprocess.Popen[bytes]) -> tuple[bool, dict[str, object]]:
    taskkill = shutil.which("taskkill")
    if not taskkill:
        try:
            process.kill()
            process.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return False, {"method": "taskkill", "reason": "taskkill_unavailable"}

    try:
        result = subprocess.run(
            [taskkill, "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            shell=False,
            timeout=_TERMINATION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, {"method": "taskkill", "error_type": type(error).__name__}

    try:
        process.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return False, {
            "method": "taskkill",
            "exit_code": result.returncode,
            "reason": "process_still_running",
        }
    return result.returncode == 0, {"method": "taskkill", "exit_code": result.returncode}


def _terminate_posix_tree(process: subprocess.Popen[bytes]) -> tuple[bool, dict[str, object]]:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as error:
        return False, {"method": "killpg", "error_type": type(error).__name__}
    try:
        process.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return False, {"method": "killpg", "reason": "process_still_running"}
    return process.poll() is not None, {"method": "killpg"}


def terminate_process_tree(process: subprocess.Popen[bytes]) -> tuple[bool, dict[str, object]]:
    if os.name == "nt":
        return _terminate_windows_tree(process)
    return _terminate_posix_tree(process)


def execute_process(
    executable: Path,
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> dict[str, object]:
    stdout_capture = BoundedCapture()
    stderr_capture = BoundedCapture()
    popen_kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "env": environment,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        "bufsize": 0,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen([str(executable), *argv], **popen_kwargs)
    except OSError as error:
        return {
            "started": False,
            "start_error_type": type(error).__name__,
            "start_error_message": str(error)[:1000],
            "stdout_capture": stdout_capture,
            "stderr_capture": stderr_capture,
        }

    if process.stdout is None or process.stderr is None:
        raise RuntimeError("subprocess pipes were not created")
    streams = [(process.stdout, stdout_capture), (process.stderr, stderr_capture)]
    timed_out = False
    termination_succeeded = True
    termination_evidence: dict[str, object] | None = None

    try:
        # Python >=3.12 supports nonblocking anonymous pipes on Windows and POSIX.
        # FileIO + os.read avoids BufferedReader's uninterruptible read/close lock.
        for stream, _ in streams:
            os.set_blocking(stream.fileno(), False)
        try:
            exit_code = _wait_with_capture(process, streams, timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                termination_succeeded, termination_evidence = terminate_process_tree(process)
            except (OSError, subprocess.SubprocessError) as error:
                termination_succeeded = False
                termination_evidence = {"error_type": type(error).__name__}
            exit_code = process.poll()
            termination_evidence = dict(termination_evidence or {})
            termination_evidence["termination_succeeded"] = termination_succeeded
            termination_evidence["process_still_running"] = exit_code is None
        _finish_streams(streams)
    finally:
        # There is no concurrent read: closing these owned read handles cannot
        # wait for an inherited writer. Surviving processes remain failures.
        for stream, capture in ((process.stdout, stdout_capture), (process.stderr, stderr_capture)):
            try:
                stream.close()
            except OSError:
                capture.mark_incomplete()

    return {
        "started": True,
        "pid": process.pid,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "termination_succeeded": termination_succeeded,
        "termination_evidence": termination_evidence,
        "stdout_capture": stdout_capture,
        "stderr_capture": stderr_capture,
    }
