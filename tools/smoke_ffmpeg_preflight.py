from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.status import LightweightEngineStatus  # noqa: E402
from utils import ffmpeg_tools  # noqa: E402
import engine.pipeline_worker as pipeline_worker  # noqa: E402
import controllers.task_controller as task_controller  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    original_which = ffmpeg_tools.shutil.which
    try:
        ffmpeg_tools.shutil.which = lambda name: f"C:/tools/{name}.exe"  # type: ignore[method-assign]
        ready = ffmpeg_tools.inspect_ffmpeg()
        _require(ready["ffmpeg"] and ready["ffprobe"], "both FFmpeg tools should be detected")
        _require("已就绪" in ffmpeg_tools.ffmpeg_status_text(ready), "ready status should be user-readable")

        ffmpeg_tools.shutil.which = lambda name: "C:/tools/ffmpeg.exe" if name == "ffmpeg" else None  # type: ignore[method-assign]
        missing_probe = ffmpeg_tools.inspect_ffmpeg()
        _require(missing_probe["ffmpeg"] and not missing_probe["ffprobe"], "missing ffprobe should be detected")
        _require("ffprobe" in ffmpeg_tools.ffmpeg_status_text(missing_probe), "missing tool should be named")
    finally:
        ffmpeg_tools.shutil.which = original_which  # type: ignore[method-assign]

    original_engine = pipeline_worker.inspect_engine_lightweight
    original_inspect_ffmpeg = pipeline_worker.inspect_ffmpeg
    try:
        pipeline_worker.inspect_engine_lightweight = lambda: LightweightEngineStatus(  # type: ignore[assignment]
            label="需要安装 Next 引擎",
            detail="fixture engine missing",
            ready=False,
        )
        engine_preview = pipeline_worker.build_pipeline_preview({"media_path": __file__, "settings": {}})
        _require(not engine_preview["ok"], "missing engine must block preview")
        _require(any("AI 引擎未就绪" in line for line in engine_preview["lines"]), "engine warning is missing")

        pipeline_worker.inspect_engine_lightweight = lambda: LightweightEngineStatus(
            label="Next 独立引擎就绪",
            detail="fixture engine ready",
            ready=True,
        )
        pipeline_worker.inspect_ffmpeg = lambda: {"ffmpeg": None, "ffprobe": None}  # type: ignore[assignment]
        ffmpeg_preview = pipeline_worker.build_pipeline_preview({"media_path": __file__, "settings": {}})
        _require(not ffmpeg_preview["ok"], "missing FFmpeg must block preview")
        _require(any("外部依赖未就绪" in line for line in ffmpeg_preview["lines"]), "FFmpeg warning is missing")
    finally:
        pipeline_worker.inspect_engine_lightweight = original_engine  # type: ignore[assignment]
        pipeline_worker.inspect_ffmpeg = original_inspect_ffmpeg  # type: ignore[assignment]

    app = SimpleNamespace(logs=[], results=[])
    app.append_log = lambda message: app.logs.append(str(message))
    app.mark_task_finished = lambda message, success: app.results.append((str(message), bool(success)))
    controller = task_controller.TaskController(app)
    original_controller_engine = task_controller.inspect_engine_lightweight
    original_controller_ffmpeg = task_controller.inspect_ffmpeg
    try:
        task_controller.inspect_engine_lightweight = lambda: LightweightEngineStatus(
            label="需要安装 Next 引擎", detail="fixture engine missing", ready=False
        )
        _require(not controller._dependencies_ready(), "controller should block missing engine")
        _require(app.results and app.results[-1][1] is False, "blocked engine should publish failed task state")

        task_controller.inspect_engine_lightweight = lambda: LightweightEngineStatus(
            label="Next 独立引擎就绪", detail="fixture engine ready", ready=True
        )
        task_controller.inspect_ffmpeg = lambda: {"ffmpeg": None, "ffprobe": None}  # type: ignore[assignment]
        _require(not controller._dependencies_ready(), "controller should block missing FFmpeg")
        _require("ffprobe" in app.logs[-1], "blocked FFmpeg should name the missing tool")
    finally:
        task_controller.inspect_engine_lightweight = original_controller_engine  # type: ignore[assignment]
        task_controller.inspect_ffmpeg = original_controller_ffmpeg  # type: ignore[assignment]

    print("PASS: pipeline preview blocks missing engine or FFmpeg with actionable messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
