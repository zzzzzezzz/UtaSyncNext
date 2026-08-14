from __future__ import annotations

import os
import signal
import subprocess
import sys


def kill_process_tree(pid: int) -> None:
    """Terminate a worker process tree while preserving the no-console-window rule."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,
            )
        except Exception:
            pass
        return

    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
