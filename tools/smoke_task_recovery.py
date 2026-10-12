from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.task_recovery import inspect_recovery_state, write_failure_reports  # noqa: E402
from ui.log_formatter import format_console_line  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="utasyncnext-task-recovery-") as temp_dir:
        root = Path(temp_dir) / "output"
        media = Path(temp_dir) / "fixture.mp4"
        media.write_bytes(b"fixture")
        subtitles = root / "fixture" / "subtitles"
        audio = root / "fixture" / "audio"
        subtitles.mkdir(parents=True)
        audio.mkdir(parents=True)
        (subtitles / "fixture_极致打轴.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        (subtitles / "fixture_极致打轴_翻译缓存.json").write_text('{"done":"ok"}', encoding="utf-8")
        (subtitles / "fixture_打轴指纹.hash").write_text("fingerprint", encoding="utf-8")
        (audio / "source.wav").write_bytes(b"audio")

        state = inspect_recovery_state(str(media), root)
        _require(state["resume_available"], "valid source SRT should be resumable")
        _require(state["can_reuse_audio"], "audio cache should be reusable")
        _require(state["can_skip_asr"], "valid source SRT and fingerprint should skip ASR")
        _require(state["can_resume_translation"], "translation batches should resume after verified ASR")
        _require(state["resume_scope"] == "translation", "resume scope should name the furthest safe stage")
        _require(state["has_translation_cache"], "partial translation cache should be detected")
        _require("原文打轴" in state["reusable_parts"], "source SRT should be named in recovery result")

        log_path, record_path, report = write_failure_reports(
            payload={"media_path": str(media), "settings": {"api_key": "fake-key", "model_size": "large-v2"}},
            error_text="API_KEY=fake-key failed",
            run_log=["request api_key: fake-key\n", "still working\n"],
            last_status="翻译：正在处理。",
            output_root=root,
        )
        log_text = log_path.read_text(encoding="utf-8")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        _require("fake-key" not in log_text, "error log must redact API key")
        _require("fake-key" not in record_path.read_text(encoding="utf-8"), "recovery record must redact API key")
        _require("恢复建议" in log_text and "原文打轴" in log_text, "error log should explain reusable work")
        _require(record["recovery"]["resume_available"], "record should preserve recovery state")
        _require(report["artifacts"].get("srt"), "recovery should expose source SRT")

        audio_only_media = Path(temp_dir) / "audio-only.mp4"
        audio_only_media.write_bytes(b"fixture")
        audio_only_dir = root / "audio-only" / "audio"
        audio_only_dir.mkdir(parents=True)
        (audio_only_dir / "source.wav").write_bytes(b"audio")
        audio_only = inspect_recovery_state(str(audio_only_media), root)
        _require(audio_only["resume_available"], "audio cache should still be reported as reusable work")
        _require(audio_only["can_reuse_audio"], "audio-only recovery should reuse extraction")
        _require(not audio_only["can_skip_asr"], "audio-only recovery must not claim ASR resume")
        _require(not audio_only["can_resume_translation"], "audio-only recovery must not claim translation resume")
        _require(audio_only["resume_scope"] == "audio_only", "audio-only recovery scope should be explicit")
        _require("从头听写" in audio_only["advice"], "audio-only advice must explain that ASR restarts")

        formatted_resume = format_console_line("⏩ [断点续传] 打轴指纹匹配，直接读取: fixture.srt")
        _require("上次打轴已完整保存" in formatted_resume, "resume log should name the completed stage")
        _require("跳过重新识别" in formatted_resume, "resume log should explain what was skipped")

        empty_state = inspect_recovery_state(str(Path(temp_dir) / "empty.mp4"), root)
        _require(not empty_state["resume_available"], "missing artifacts must not claim resumability")

    print("PASS: task failure reports explain safe resume state and redact secrets")
    return 0


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
