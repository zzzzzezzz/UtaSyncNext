from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance_lock import SingleInstanceLock  # noqa: E402


def main() -> int:
    name = "Local\\UtaSyncNext.SmokeInstanceLock"
    first = SingleInstanceLock.acquire(name)
    _require(first is not None, "first instance did not acquire the lock")
    second = SingleInstanceLock.acquire(name)
    _require(second is None, "second instance acquired the lock")

    first.release()
    third = SingleInstanceLock.acquire(name)
    _require(third is not None, "lock was not released after the first instance exited")
    third.release()
    print("PASS: UtaSyncNext allows only one UI instance and releases the lock cleanly")
    return 0


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
