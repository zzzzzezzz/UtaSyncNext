from __future__ import annotations

import argparse
import hashlib
import multiprocessing as mp
import queue
import sys
import time
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.pipeline_worker import run_pipeline_worker  # noqa: E402
from engine.protocol import MSG_DONE, MSG_ERROR, MSG_STATUS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interrupt a real local ASR task after SRT completion, then verify restart cache reuse."
    )
    parser.add_argument("--media", required=True, help="Unique staged media path used for the cold run.")
    parser.add_argument("--model", default="large-v2")
    parser.add_argument("--timeout", type=int, default=2400)
    args = parser.parse_args()

    media = Path(args.media).resolve()
    if not media.is_file():
        print(f"FAIL: media not found: {media}")
        return 1

    output_dir = NEXT_ROOT / "output" / media.stem
    if output_dir.exists():
        print(f"FAIL: cold-run output already exists: {output_dir}")
        return 1

    first_payload = _payload(media, args.model, interrupted_translation=True)
    print("PHASE_1: cold ASR, interrupt before translation request")
    first = _run_worker(
        first_payload,
        timeout=args.timeout,
        cancel_on_status="正在调用大模型翻译",
    )
    if not first["cancel_triggered"]:
        print("FAIL: interruption point was not reached")
        return 1

    subtitles = output_dir / "subtitles"
    source_srt = subtitles / f"{media.stem}_极致打轴.srt"
    fingerprint = subtitles / f"{media.stem}_打轴指纹.hash"
    if not _valid_srt(source_srt) or not fingerprint.is_file():
        print("FAIL: interrupted run did not preserve source SRT and ASR fingerprint")
        return 1
    before_hash = _sha256(source_srt)
    before_mtime = source_srt.stat().st_mtime_ns

    print("PHASE_2: restart without API, expect ASR cache hit")
    restart_started = time.monotonic()
    second = _run_worker(_payload(media, args.model, interrupted_translation=False), timeout=120)
    restart_elapsed = time.monotonic() - restart_started
    done_messages = [message for message in second["messages"] if message.get("type") == MSG_DONE]
    if not done_messages:
        print("FAIL: restarted task did not complete")
        return 1
    summary = done_messages[-1].get("summary")
    if not isinstance(summary, dict) or summary.get("asr_cache_hit") is not True:
        print(f"FAIL: restarted task did not report ASR cache reuse: {summary}")
        return 1
    if _sha256(source_srt) != before_hash or source_srt.stat().st_mtime_ns != before_mtime:
        print("FAIL: restart rewrote the completed source SRT instead of reusing it")
        return 1

    artifacts = done_messages[-1].get("artifacts")
    if not isinstance(artifacts, dict) or Path(str(artifacts.get("srt") or "")).resolve() != source_srt.resolve():
        print(f"FAIL: restart returned unexpected artifacts: {artifacts}")
        return 1

    print("PASS: long task resumed from completed ASR after interruption")
    print(f"- source_srt: {source_srt}")
    print(f"- source_srt_sha256: {before_hash}")
    print(f"- restart_elapsed_seconds: {restart_elapsed:.2f}")
    print(f"- first_run_errors: {sum(message.get('type') == MSG_ERROR for message in first['messages'])}")
    print("- external_api_requests: 0 (cancellation target uses loopback-only URL)")
    return 0


def _payload(media: Path, model: str, *, interrupted_translation: bool) -> dict[str, object]:
    api_settings: dict[str, str] = {
        "api_key": "",
        "api_url": "",
        "api_model": "",
        "proxy_url": "",
    }
    if interrupted_translation:
        api_settings = {
            "api_key": "fake-key",
            "api_url": "http://127.0.0.1:9/v1",
            "api_model": "resume-test-model",
            "proxy_url": "",
        }
    return {
        "media_path": str(media),
        "lyrics": "",
        "settings": {
            "audio_type": "Live 现场演唱 (常规/摇滚)",
            "model_size": model,
            "hardware_mode": "极限防爆",
            "use_demucs": "先跳过 Demucs (极速/推荐先出轴)",
            "output_mode": "中日双语字幕",
            "ass_preset": "不生成 (仅SRT)",
            **api_settings,
        },
    }


def _run_worker(
    payload: dict[str, object],
    *,
    timeout: int,
    cancel_on_status: str = "",
) -> dict[str, object]:
    msg_queue: mp.Queue = mp.Queue()
    cancel_event = mp.Event()
    process = mp.Process(target=run_pipeline_worker, args=(msg_queue, cancel_event, payload))
    process.start()
    messages: list[dict[str, object]] = []
    cancel_triggered = False
    started = time.monotonic()

    while process.is_alive():
        if time.monotonic() - started > timeout:
            cancel_event.set()
            process.terminate()
            process.join(timeout=5)
            raise RuntimeError(f"worker timeout after {timeout}s")
        try:
            message = msg_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        messages.append(message)
        _print_message(message)
        if (
            cancel_on_status
            and message.get("type") == MSG_STATUS
            and cancel_on_status in str(message.get("text") or "")
        ):
            cancel_event.set()
            cancel_triggered = True

    process.join(timeout=5)
    while True:
        try:
            message = msg_queue.get_nowait()
        except queue.Empty:
            break
        messages.append(message)
        _print_message(message)
    return {"messages": messages, "cancel_triggered": cancel_triggered}


def _print_message(message: dict[str, object]) -> None:
    message_type = str(message.get("type") or "")
    if message_type in {MSG_STATUS, MSG_DONE, MSG_ERROR}:
        print(f"{message_type}: {message.get('text')}", flush=True)


def _valid_srt(path: Path) -> bool:
    return path.is_file() and "-->" in path.read_text(encoding="utf-8", errors="ignore")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
