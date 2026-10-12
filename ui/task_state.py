"""Presentation-level task state shared by the Next UI and controller bridge.

This module deliberately contains no Tk/customtkinter imports.  It is the small
contract that lets the UI be rearranged without changing worker semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path


class TaskPhase(str, Enum):
    EMPTY = "empty"
    READY = "ready"
    PREFLIGHT = "preflight"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


PHASE_LABELS: dict[TaskPhase, str] = {
    TaskPhase.EMPTY: "未选择媒体",
    TaskPhase.READY: "准备就绪",
    TaskPhase.PREFLIGHT: "任务预检",
    TaskPhase.QUEUED: "排队中",
    TaskPhase.RUNNING: "处理中",
    TaskPhase.CANCELLING: "取消中",
    TaskPhase.SUCCEEDED: "已完成",
    TaskPhase.FAILED: "失败",
    TaskPhase.CANCELLED: "已取消",
}


@dataclass(frozen=True)
class TaskSnapshot:
    """Immutable UI snapshot for the currently selected task."""

    phase: TaskPhase = TaskPhase.EMPTY
    progress: float = 0.0
    media_name: str = ""
    detail: str = "请先导入视频或音频文件。"

    @classmethod
    def initial(cls, media_path: str | Path = "") -> "TaskSnapshot":
        media_name = Path(str(media_path)).name if str(media_path).strip() else ""
        if media_name:
            return cls(
                phase=TaskPhase.READY,
                media_name=media_name,
                detail="媒体已载入，可以启动同步任务。",
            )
        return cls()

    @property
    def phase_label(self) -> str:
        return PHASE_LABELS[self.phase]

    @property
    def is_active(self) -> bool:
        return self.phase in {
            TaskPhase.PREFLIGHT,
            TaskPhase.QUEUED,
            TaskPhase.RUNNING,
            TaskPhase.CANCELLING,
        }

    @property
    def can_start(self) -> bool:
        return bool(self.media_name) and not self.is_active

    @property
    def can_cancel(self) -> bool:
        return self.phase in {TaskPhase.QUEUED, TaskPhase.RUNNING, TaskPhase.CANCELLING}

    def evolve(
        self,
        *,
        phase: TaskPhase | None = None,
        progress: float | None = None,
        detail: str | None = None,
        media_path: str | Path | None = None,
    ) -> "TaskSnapshot":
        """Return a normalized copy suitable for rendering by any UI layout."""
        media_name = self.media_name
        if media_path is not None:
            media_name = Path(str(media_path)).name if str(media_path).strip() else ""
        next_progress = self.progress if progress is None else max(0.0, min(1.0, float(progress)))
        return replace(
            self,
            phase=phase or self.phase,
            progress=next_progress,
            media_name=media_name,
            detail=self.detail if detail is None else str(detail),
        )


def snapshot_for_media(media_path: str | Path) -> TaskSnapshot:
    """Small convenience used by callers that do not need the classmethod."""
    return TaskSnapshot.initial(media_path)
