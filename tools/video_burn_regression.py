from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from threading import Event, Timer


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from utils.ffmpeg_tools import FFmpegSubtitleBurner, safe_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and record a real subtitle burn regression.")
    parser.add_argument("--media", required=True)
    parser.add_argument("--ass", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--hires", action="store_true")
    parser.add_argument("--cancel-after", type=float, default=0.0)
    parser.add_argument("--fonts-dir", default="", help="Optional FFmpeg/libass font directory")
    args = parser.parse_args()

    media = Path(args.media).resolve()
    ass = Path(args.ass).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_name = "BURN_CANCEL_REPORT.json" if args.cancel_after > 0 else "BURN_REPORT.json"
    if not media.is_file() or not ass.is_file():
        print("FAIL: source media or ASS is missing.")
        return 1

    logs: list[str] = []
    progress: list[dict[str, object]] = []
    started = time.monotonic()
    cancel_event = Event()
    cancel_requested_at: float | None = None

    def request_cancel() -> None:
        nonlocal cancel_requested_at
        cancel_requested_at = time.monotonic()
        cancel_event.set()

    cancel_timer = Timer(args.cancel_after, request_cancel) if args.cancel_after > 0 else None
    cancel_timer_started = False

    def on_log(value: str) -> None:
        nonlocal cancel_timer_started
        clean = value.strip()
        if clean:
            logs.append(clean)
            print(clean, flush=True)
        if cancel_timer and not cancel_timer_started:
            cancel_timer.start()
            cancel_timer_started = True

    def on_progress(value: float, text: str) -> None:
        percent = round(max(0.0, min(1.0, value)) * 100, 1)
        if not progress or int(percent) != int(float(progress[-1]["percent"])):
            progress.append({"percent": percent, "text": text})

    try:
        output = Path(
            FFmpegSubtitleBurner.burn(
                media_path=str(media),
                ass_path=str(ass),
                output_dir=str(output_dir),
                hires_enabled=bool(args.hires),
                fonts_dir=args.fonts_dir or None,
                cancel_event=cancel_event,
                progress_cb=on_progress,
                log_cb=on_log,
            )
        )
        probe = _probe(output)
        report = {
            "status": "passed",
            "media": str(media),
            "media_sha256": _sha256(media),
            "ass": str(ass),
            "ass_sha256": _sha256(ass),
            "output": str(output),
            "output_sha256": _sha256(output),
            "hires": bool(args.hires),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "logs": logs,
            "progress_samples": progress,
            "probe": probe,
            "temporary_files_remaining": [
                str(path)
                for path in output_dir.iterdir()
                if path.name == "_utasyncnext_burn.ass" or "_处理中" in path.name
            ],
        }
        report_path = output_dir.parent / report_name
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: subtitle burn completed and validated.")
        print(f"- output: {output}")
        print(f"- report: {report_path}")
        return 0
    except Exception as exc:
        temporary_files = [
            str(path)
            for path in output_dir.iterdir()
            if path.name == "_utasyncnext_burn.ass" or "_处理中" in path.name
        ]
        report = {
            "status": "cancelled_passed" if args.cancel_after > 0 and "取消" in str(exc) and not temporary_files else "failed",
            "media": str(media),
            "ass": str(ass),
            "hires": bool(args.hires),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "logs": logs,
            "error": str(exc),
            "cancel_response_seconds": (
                round(time.monotonic() - cancel_requested_at, 2) if cancel_requested_at is not None else None
            ),
            "temporary_files_remaining": temporary_files,
        }
        report_path = output_dir.parent / report_name
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["status"] == "cancelled_passed":
            print("PASS: cancellation removed the incomplete output.")
            return 0
        print(f"FAIL: {exc}")
        return 1
    finally:
        if cancel_timer and cancel_timer_started:
            cancel_timer.cancel()


def _probe(path: Path) -> dict[str, object]:
    result = safe_run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=index,codec_type,codec_name,profile,width,height,pix_fmt,avg_frame_rate,sample_rate,channels:stream_tags=ENCODER",
            "-of",
            "json",
            str(path),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("FFprobe could not read the completed output.")
    return json.loads(result.stdout or "{}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
