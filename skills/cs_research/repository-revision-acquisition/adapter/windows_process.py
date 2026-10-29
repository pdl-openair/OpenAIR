"""Windows-only bounded process-tree execution for Git adapter calls."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import time
from typing import Mapping, Sequence

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_ERROR_NO_MORE_FILES = 18
_ERROR_MORE_DATA = 234
_WINDOWS_TREE_REAP_SECONDS = 5.0


class _JobObjectBasicLimitInformation(ctypes.Structure):
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
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _raise_last_windows_error(operation: str) -> None:
    error = ctypes.WinError(ctypes.get_last_error())
    raise OSError(error.errno, f"{operation}: {error.strerror}")


def _close_handle(handle: int) -> None:
    if handle:
        _kernel32.CloseHandle(handle)


def _create_job_object() -> int:
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        _raise_last_windows_error("CreateJobObjectW")
    info = _JobObjectExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        _close_handle(job)
        raise ctypes.WinError(error)
    return job


def _assign_process(job: int, pid: int) -> None:
    process_handle = _kernel32.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
    )
    if not process_handle:
        _raise_last_windows_error("OpenProcess")
    try:
        if not _kernel32.AssignProcessToJobObject(job, process_handle):
            _raise_last_windows_error("AssignProcessToJobObject")
    finally:
        _close_handle(process_handle)


def _resume_suspended_process(pid: int) -> None:
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        _raise_last_windows_error("CreateToolhelp32Snapshot")
    resumed = 0
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        if not _kernel32.Thread32First(snapshot, ctypes.byref(entry)):
            if ctypes.get_last_error() != _ERROR_NO_MORE_FILES:
                _raise_last_windows_error("Thread32First")
        else:
            while True:
                if entry.th32OwnerProcessID == pid:
                    thread = _kernel32.OpenThread(
                        _THREAD_SUSPEND_RESUME, False, entry.th32ThreadID
                    )
                    if not thread:
                        _raise_last_windows_error("OpenThread")
                    try:
                        if _kernel32.ResumeThread(thread) == 0xFFFFFFFF:
                            _raise_last_windows_error("ResumeThread")
                        resumed += 1
                    finally:
                        _close_handle(thread)
                entry.dwSize = ctypes.sizeof(entry)
                if not _kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                    if ctypes.get_last_error() != _ERROR_NO_MORE_FILES:
                        _raise_last_windows_error("Thread32Next")
                    break
    finally:
        _close_handle(snapshot)
    if resumed == 0:
        raise OSError(f"No suspended thread found for process {pid}")


def _job_process_ids(job: int) -> set[int]:
    capacity = 8
    while capacity <= 4096:
        class _ProcessIdList(ctypes.Structure):
            _fields_ = [
                ("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * capacity),
            ]

        value = _ProcessIdList()
        if not _kernel32.QueryInformationJobObject(
            job,
            _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
            ctypes.byref(value),
            ctypes.sizeof(value),
            None,
        ):
            error = ctypes.get_last_error()
            if error == _ERROR_MORE_DATA:
                capacity = max(capacity * 2, int(value.NumberOfAssignedProcesses))
                continue
            _raise_last_windows_error("QueryInformationJobObject")
        if value.NumberOfProcessIdsInList >= value.NumberOfAssignedProcesses:
            return {
                int(value.ProcessIdList[index])
                for index in range(value.NumberOfProcessIdsInList)
            }
        capacity = max(capacity * 2, int(value.NumberOfAssignedProcesses))
    raise OSError("Windows job process list exceeded safety bound")


def _wait_for_descendants(job: int, root_pid: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        descendants = _job_process_ids(job) - {root_pid}
        if not descendants:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))


def _terminate_and_reap(
    job: int,
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float,
) -> tuple[str, str]:
    if not _kernel32.TerminateJobObject(job, 1):
        _raise_last_windows_error("TerminateJobObject")
    deadline = time.monotonic() + timeout_seconds
    try:
        stdout, stderr = process.communicate(timeout=max(deadline - time.monotonic(), 0.001))
    except subprocess.TimeoutExpired as exc:
        raise OSError("Timed out while reaping terminated Windows process tree") from exc
    if not _wait_for_descendants(job, process.pid, max(deadline - time.monotonic(), 0.0)):
        raise OSError("Timed out waiting for Windows process-tree descendants to exit")
    return stdout, stderr


def run_windows_process_tree(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    encoding: str,
    errors: str,
) -> subprocess.CompletedProcess[str]:
    """Run one command in a race-free Windows Job Object process tree."""
    if os.name != "nt":
        raise OSError("Windows process-tree runner called on non-Windows platform")

    job = _create_job_object()
    process: subprocess.Popen[str] | None = None
    assigned_to_job = False
    deadline = time.monotonic() + timeout_seconds
    try:
        # Start suspended so Git cannot spawn a helper before it is contained by the Job.
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=dict(env),
            text=True,
            encoding=encoding,
            errors=errors,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=_CREATE_SUSPENDED,
        )
        _assign_process(job, process.pid)
        assigned_to_job = True
        _resume_suspended_process(process.pid)

        try:
            stdout, stderr = process.communicate(
                timeout=max(deadline - time.monotonic(), 0.001)
            )
            if not _wait_for_descendants(
                job, process.pid, max(deadline - time.monotonic(), 0.0)
            ):
                raise subprocess.TimeoutExpired(list(command), timeout_seconds)
        except subprocess.TimeoutExpired:
            stdout, stderr = _terminate_and_reap(
                job, process, timeout_seconds=_WINDOWS_TREE_REAP_SECONDS
            )
            raise subprocess.TimeoutExpired(
                list(command),
                timeout_seconds,
                output=stdout,
                stderr=stderr,
            )

        return subprocess.CompletedProcess(
            list(command), process.returncode, stdout=stdout, stderr=stderr
        )
    finally:
        if process is not None and process.poll() is None:
            try:
                if assigned_to_job:
                    _kernel32.TerminateJobObject(job, 1)
                else:
                    process.kill()
                process.wait(timeout=_WINDOWS_TREE_REAP_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                pass
        _close_handle(job)
