from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.task_state import TaskPhase, TaskSnapshot, snapshot_for_media


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    empty = TaskSnapshot.initial()
    require(empty.phase is TaskPhase.EMPTY, "empty snapshot phase mismatch")
    require(not empty.can_start and not empty.can_cancel, "empty snapshot actions mismatch")

    ready = snapshot_for_media(r"E:\media\demo.mp4")
    require(ready.phase is TaskPhase.READY, "media snapshot should be ready")
    require(ready.media_name == "demo.mp4", "media name should be reduced to basename")
    require(ready.can_start and not ready.can_cancel, "ready snapshot actions mismatch")

    running = ready.evolve(phase=TaskPhase.RUNNING, progress=1.7, detail="正在翻译")
    require(running.progress == 1.0, "progress must be clamped to one")
    require(running.is_active and running.can_cancel, "running snapshot actions mismatch")
    require(running.phase_label == "处理中", "running label mismatch")

    cancelled = running.evolve(phase=TaskPhase.CANCELLED, progress=-0.5, detail="任务已取消")
    require(cancelled.progress == 0.0, "progress must be clamped to zero")
    require(not cancelled.is_active and cancelled.can_start, "cancelled snapshot should be restartable")
    require(Path(cancelled.media_name).name == "demo.mp4", "media name should survive transitions")

    print("PASS: task state snapshots and action flags are valid")


if __name__ == "__main__":
    main()
