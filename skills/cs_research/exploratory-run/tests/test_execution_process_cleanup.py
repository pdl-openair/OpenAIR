"""C task acceptance: real pipes, supervised deadlines, and actual adapter records.

Authority: CS workflow execution facts/local failure and C task D1-D4.
Protected outcome: bounded return, stable captured output, honest termination facts.
Responsibility: component behavior + real runtime resilience, isolated from services.
These are task acceptance assets; no claim of complete product-chain verification.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from adapter import execution_common as common  # noqa: E402
from adapter import execution_process as runtime  # noqa: E402

SCHEDULING_TOLERANCE_SECONDS = 1.5


def supervise_cleanup_case(case: str, directory: Path) -> dict[str, Any]:
    """A watchdog always releases the owned writer, even if production hangs."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SKILL_ROOT)
    environment["PYTHONIOENCODING"] = "utf-8"
    with subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), case, str(directory)],
        cwd=directory,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as supervisor:
        try:
            stdout, stderr = supervisor.communicate(timeout=45 if case == "repeat" else 30)
            assert supervisor.returncode == 0, stderr
            result = json.loads(stdout)
            assert result["case"] == case
            return result
        finally:
            # Every diagnostic child checks this unique directory's release file
            # and has its own 15s hard lifetime. No PID search or broad kill.
            (directory / "release").touch()
            try:
                supervisor.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                supervisor.kill()
                supervisor.communicate(timeout=4)


def _handles() -> int:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        count = ctypes.c_ulong()
        if not kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count)):
            raise ctypes.WinError(ctypes.get_last_error())
        return count.value
    return len(list(Path("/proc/self/fd").iterdir()))


def _await_ready(path: Path) -> None:
    deadline = time.monotonic() + 3
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), "child did not reach the output/writer synchronization point"


def _child_program(directory: Path, busy: bool = False) -> str:
    return f"""
import pathlib, sys, time
root = pathlib.Path({str(directory)!r})
sys.stdout.write('stdout-before-timeout\\n'); sys.stdout.flush()
sys.stderr.write('stderr-before-timeout\\n'); sys.stderr.flush()
(root / 'ready').touch()
deadline = time.monotonic() + 15
try:
    while not (root / 'release').exists() and time.monotonic() < deadline:
        if {busy!r}:
            sys.stdout.write('x' * 8192); sys.stdout.flush()
            sys.stderr.write('y' * 8192); sys.stderr.flush()
        else:
            time.sleep(0.01)
except BrokenPipeError:
    pass
"""


def _assert_root_result(result: dict[str, Any], case: str) -> list[common.BoundedCapture]:
    captures = [result["stdout_capture"], result["stderr_capture"]]
    for name, capture in zip(("stdout", "stderr"), captures, strict=True):
        record = capture.as_record(())
        assert f"{name}-" in record["text"]
        assert record["stream_complete"] == (case == "eof")
        assert record["captured_bytes"] <= common.CAPTURE_LIMIT_BYTES
        if case == "busy":
            assert record["truncated"] is True
    if case == "eof":
        assert result["exit_code"] == 0
        assert result["timed_out"] is False
    else:
        assert result["timed_out"] is True
        assert result["termination_succeeded"] is False
        assert result["exit_code"] is None
        assert result["termination_evidence"]["process_still_running"] is True
    return captures


def _one_run(case: str, directory: Path) -> dict[str, Any]:
    processes: list[subprocess.Popen[bytes]] = []
    ready_times: list[float] = []
    real_popen = subprocess.Popen
    grace = runtime._STREAM_DRAIN_GRACE_SECONDS
    termination_budget = (
        2 * runtime._TERMINATION_TIMEOUT_SECONDS if case == "adapter_timeout" else 0
    )
    bound = 1 + termination_budget + grace + SCHEDULING_TOLERANCE_SECONDS
    threads_before = set(threading.enumerate())
    handles_before = _handles()

    def tracked_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        processes.append(process)
        if len(processes) == 1:
            if case != "eof":
                _await_ready(directory / "ready")
            ready_times.append(time.monotonic())
        return process

    def refused_termination(process: subprocess.Popen[bytes]) -> tuple[bool, dict[str, object]]:
        _await_ready(directory / "ready")
        assert process.poll() is None
        if "exception" in case:
            raise OSError("controlled termination refusal")
        return False, {"method": "controlled_refusal", "reason": "writer_still_open"}

    result: dict[str, Any] = {}
    captures: list[common.BoundedCapture] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runtime.subprocess, "Popen", tracked_popen)
        if case != "eof" and case != "adapter_timeout":
            patch.setattr(runtime, "terminate_process_tree", refused_termination)
        try:
            if case.startswith("adapter_"):
                from adapter import exploratory_run_adapter as adapter
                from test_exploratory_run_adapter import _prepare_workspace, _record, _request

                executable = _prepare_workspace(directory, patch)
                result = adapter.run_exploratory_run(
                    _request(executable, argv=["-c", _child_program(directory)], timeout_seconds=1)
                )
                elapsed = time.monotonic() - ready_times[0]
                persisted = _record(directory, result)
                expected = "timeout" if case == "adapter_timeout" else "termination_failed"
                assert result["state"] == persisted["state"] == "failed"
                assert result["execution_status"] == persisted["execution_status"] == expected, (
                    persisted
                )
                assert persisted["timed_out"] is True
                assert persisted["termination_evidence"]["termination_succeeded"] == (
                    case == "adapter_timeout"
                )
                for stream in ("stdout", "stderr"):
                    assert f"{stream}-before-timeout" in persisted[stream]["text"]
                    assert result[stream] == persisted[stream]
                    assert persisted[stream]["stream_complete"] == (case == "adapter_timeout")
            else:
                program = (
                    "import sys; print('stdout-eof'); sys.stderr.write('stderr-eof\\n')"
                    if case == "eof"
                    else _child_program(directory, case == "busy")
                )
                result = runtime.execute_process(
                    Path(sys.executable),
                    ["-c", program],
                    cwd=directory,
                    environment=os.environ.copy(),
                    timeout_seconds=1,
                )
                elapsed = time.monotonic() - ready_times[0]
                captures = _assert_root_result(result, case)
            assert elapsed <= bound, (elapsed, bound)
            if case not in ("eof", "adapter_timeout", "busy"):
                assert processes[-1].poll() is None, "writer must still be open at return"
            for process in processes:
                assert process.stdout is not None and process.stdout.closed
                assert process.stderr is not None and process.stderr.closed
            assert set(threading.enumerate()) == threads_before
            snapshots = [capture.as_record(()) for capture in captures]
        finally:
            (directory / "release").touch()
            for process in processes:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                # Popen keeps its Windows process handle until the object is freed.
                if os.name == "nt":
                    process._handle.Close()
            assert all(process.poll() is not None for process in processes)
        assert [capture.as_record(()) for capture in captures] == snapshots
    return {
        "case": case,
        "elapsedSeconds": elapsed,
        "boundSeconds": bound,
        "ownedProcessesReaped": len(processes),
        "readerThreadsAdded": 0,
        "handleDeltaAfterCleanup": _handles() - handles_before,
    }


def _worker(case: str, directory: Path) -> None:
    if case == "baseline_counterexample":
        result = _baseline_counterexample()
    elif case == "repeat":
        runs = []
        for index in range(4):
            run_dir = directory / str(index)
            run_dir.mkdir()
            # The same process executes repeatedly; measure actual handles/threads.
            runs.append(_one_run("open_writer", run_dir))
        assert all(run["handleDeltaAfterCleanup"] <= 0 for run in runs[1:]), runs
        result = {"case": case, "runs": runs}
    else:
        result = _one_run(case, directory)
    print(json.dumps(result))


def _baseline_counterexample() -> dict[str, Any]:
    """Run the audited implementation in memory; never replace checkout sources."""
    source = subprocess.run(
        [
            "git",
            "show",
            "b14b45e746ec7586589cb98a7f40eba0f6eabee9:"
            "skills/cs_research/exploratory-run/adapter/execution_process.py",
        ],
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout
    original = types.ModuleType("adapter._cleanup_baseline")
    original.__package__ = "adapter"
    exec(compile(source, "<audited-baseline>", "exec"), original.__dict__)
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    capture = common.BoundedCapture()
    release = threading.Event()
    reader = original._start_reader(stream, capture)

    def release_writer() -> None:
        release.wait(6)
        os.close(write_fd)

    writer = threading.Thread(target=release_writer)
    writer.start()
    try:
        started = time.monotonic()
        original._finish_reader(reader, stream, capture)
        elapsed = time.monotonic() - started
        advertised = 2 * original._STREAM_DRAIN_GRACE_SECONDS
        assert elapsed > advertised + SCHEDULING_TOLERANCE_SECONDS, elapsed
        return {
            "case": "baseline_counterexample",
            "elapsedSeconds": elapsed,
            "advertisedJoinBudgetSeconds": advertised,
            "violatedBound": True,
        }
    finally:
        release.set()
        writer.join(2)
        reader.join(2)
        stream.close()
        assert not writer.is_alive() and not reader.is_alive()


@pytest.mark.parametrize("case", ["open_writer", "exception", "eof", "busy", "repeat"])
def test_execute_process_bounded_cleanup(case: str, tmp_path: Path) -> None:
    supervise_cleanup_case(case, tmp_path)


def test_audited_baseline_violates_advertised_reader_bound(tmp_path: Path) -> None:
    assert supervise_cleanup_case("baseline_counterexample", tmp_path)["violatedBound"] is True


@pytest.mark.parametrize("case", ["adapter_failure", "adapter_exception", "adapter_timeout"])
def test_timeout_and_termination_failure_preserve_actual_adapter_record(
    case: str,
    tmp_path: Path,
) -> None:
    supervise_cleanup_case(case, tmp_path)


def test_real_pipe_open_writer_does_not_block_cleanup(tmp_path: Path) -> None:
    # Independent of process-tree termination permissions; production pipe cleanup.
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SKILL_ROOT)
    code = """
import json, os, time
from adapter.execution_common import BoundedCapture
from adapter import execution_process as p
read_fd, write_fd = os.pipe()
stream = os.fdopen(read_fd, 'rb', buffering=0)
capture = BoundedCapture()
try:
    os.set_blocking(read_fd, False)
    os.write(write_fd, b'captured-before-cleanup')
    start = time.monotonic()
    p._finish_streams([(stream, capture)])
    elapsed = time.monotonic() - start
    stream.close()
    assert elapsed <= p._STREAM_DRAIN_GRACE_SECONDS + 1.5
    assert capture.as_record(())['text'] == 'captured-before-cleanup'
    assert not capture.stream_complete
    print(json.dumps({'elapsedSeconds': elapsed, 'readHandleClosed': stream.closed}))
finally:
    stream.close()
    os.close(write_fd)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=6,
        check=True,
    )
    assert json.loads(completed.stdout)["readHandleClosed"] is True


if __name__ == "__main__":
    _worker(sys.argv[1], Path(sys.argv[2]))
