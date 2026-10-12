from __future__ import annotations

import os
import re
from pathlib import Path


INSTANCE_MUTEX_NAME = "Local\\UtaSyncNext.SingleInstance"


class SingleInstanceLock:
    """Hold the process-wide Next instance lock until the UI exits."""

    def __init__(self, *, handle: int | None = None, lock_file=None) -> None:
        self._handle = handle
        self._lock_file = lock_file

    @classmethod
    def acquire(cls, name: str = INSTANCE_MUTEX_NAME) -> "SingleInstanceLock | None":
        if os.name == "nt":
            return cls._acquire_windows(name)
        return cls._acquire_file(name)

    def release(self) -> None:
        if self._handle is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def __enter__(self) -> "SingleInstanceLock":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()

    @classmethod
    def _acquire_windows(cls, name: str) -> "SingleInstanceLock | None":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            error = ctypes.get_last_error()
            raise OSError(error, f"无法创建 Next 单实例锁: {name}")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return None
        return cls(handle=int(handle))

    @classmethod
    def _acquire_file(cls, name: str) -> "SingleInstanceLock | None":
        import fcntl

        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        path = Path(os.environ.get("TMPDIR", "/tmp")) / f"{safe_name}.lock"
        lock_file = path.open("a+")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return None
        return cls(lock_file=lock_file)
