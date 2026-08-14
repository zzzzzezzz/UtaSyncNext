from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import queue
import subprocess
import sys
import time
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NEXT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.model_registry import find_complete_snapshot, next_models_root  # noqa: E402
from engine.pipeline_worker import run_pipeline_worker  # noqa: E402
from engine.protocol import MSG_DONE, MSG_ERROR, MSG_LOG, MSG_PROGRESS, MSG_STATUS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a minimal UtaSyncNext pipeline smoke test.")
    parser.add_argument("--model", default="small", help="Local Faster-Whisper model to use.")
    parser.add_argument("--media", default="", help="Optional media path. If omitted, a short wav is generated.")
    parser.add_argument("--mode", default="live", choices=["live", "speech"], help="Acoustic mode for the smoke run.")
    parser.add_argument("--demucs", action="store_true", help="Enable Demucs vocal separation. Default skips Demucs.")
    parser.add_argument("--ass", action="store_true", help="Generate ASS after SRT. Default writes SRT only.")
    parser.add_argument("--use-settings-api", action="store_true", help="Use local user_settings.json API fields for real translation.")
    parser.add_argument("--translation-mode", default="bilingual", choices=["bilingual", "chinese_only"], help="Translation output mode when --use-settings-api is set.")
    parser.add_argument("--proxy-url", default="", help="Override translation proxy URL when --use-settings-api is set, for example 10808.")
    parser.add_argument("--lyrics-file", default="", help="Optional reference lyrics text file passed to the worker.")
    parser.add_argument("--timeout", type=int, default=600, help="Worker timeout in seconds.")
    args = parser.parse_args()

    os.chdir(NEXT_ROOT)

    model_snapshot = find_complete_snapshot(args.model, next_models_root())
    if not model_snapshot:
        print(f"FAIL: model {args.model!r} is not complete in {next_models_root()}")
        return 1

    media_path = Path(args.media).resolve() if args.media else _make_smoke_wav()
    if not media_path.exists():
        print(f"FAIL: media not found: {media_path}")
        return 1

    lyrics_text = ""
    lyrics_path: Path | None = None
    if args.lyrics_file:
        lyrics_path = Path(args.lyrics_file).resolve()
        if not lyrics_path.exists():
            print(f"FAIL: lyrics file not found: {lyrics_path}")
            return 1
        lyrics_text = lyrics_path.read_text(encoding="utf-8-sig", errors="ignore").strip()

    audio_type = "访谈 / 播客" if args.mode == "speech" else "Live 现场演唱 (常规/摇滚)"
    demucs_setting = "开启 Demucs 人声分离 (高质量/很慢)" if args.demucs else "先跳过 Demucs (极速/推荐先出轴)"
    ass_preset = "默认双语 (纯白+浅灰)" if args.ass else "不生成 (仅SRT)"
    api_settings = {"api_key": "", "api_url": "", "api_model": "", "proxy_url": ""}
    output_mode = "纯中文字幕"
    if args.use_settings_api:
        from utils.config import load_settings

        settings = load_settings()
        api_settings = {
            "api_key": str(settings.get("api_key") or "").strip(),
            "api_url": str(settings.get("api_url") or "").strip(),
            "api_model": str(settings.get("api_model") or "").strip(),
            "proxy_url": str(settings.get("proxy_url") or "").strip(),
        }
        if args.proxy_url:
            api_settings["proxy_url"] = str(args.proxy_url).strip()
        if not api_settings["api_key"]:
            print("FAIL: --use-settings-api was requested but user_settings.json has no api_key.")
            return 1
        output_mode = "中日双语字幕" if args.translation_mode == "bilingual" else "纯中文字幕"

    payload = {
        "media_path": str(media_path),
        "lyrics": lyrics_text,
        "settings": {
            "audio_type": audio_type,
            "model_size": args.model,
            "hardware_mode": "极限防爆",
            "use_demucs": demucs_setting,
            "output_mode": output_mode,
            "ass_preset": ass_preset,
            **api_settings,
        },
    }

    messages = _run_worker(payload, timeout=args.timeout)
    _print_message_summary(messages)

    errors = [message for message in messages if message.get("type") == MSG_ERROR]
    if errors:
        print(f"FAIL: worker emitted error: {errors[-1].get('text')}")
        return 1

    done_messages = [message for message in messages if message.get("type") == MSG_DONE]
    if not done_messages:
        print("FAIL: worker did not emit done message.")
        return 1

    expected_srt = NEXT_ROOT / "output" / media_path.stem / "subtitles" / f"{media_path.stem}_极致打轴.srt"
    if not _is_valid_srt(expected_srt):
        print(f"FAIL: expected SRT is missing or empty: {expected_srt}")
        return 1

    artifacts = done_messages[-1].get("artifacts")
    if not isinstance(artifacts, dict):
        print("FAIL: done message did not include output artifacts.")
        return 1
    if not artifacts.get("output_dir") or not artifacts.get("srt"):
        print(f"FAIL: done artifacts missing output_dir or srt: {artifacts}")
        return 1
    artifact_srt = Path(str(artifacts.get("srt") or ""))
    if not _is_valid_srt(artifact_srt):
        print(f"FAIL: artifact SRT is missing or empty: {artifact_srt}")
        return 1
    if args.use_settings_api and _subtitle_payload(expected_srt) == _subtitle_payload(artifact_srt):
        print("FAIL: real translation was requested but artifact SRT text did not change.")
        return 1
    if args.ass and not artifacts.get("ass"):
        print(f"FAIL: done artifacts missing ass: {artifacts}")
        return 1
    artifact_ass = Path(str(artifacts.get("ass") or "")) if args.ass else None
    if args.ass and (artifact_ass is None or not _is_valid_ass(artifact_ass)):
        print(f"FAIL: artifact ASS is missing or empty: {artifact_ass}")
        return 1

    print("PASS")
    print(f"- media: {media_path}")
    if lyrics_path:
        print(f"- lyrics: {lyrics_path}")
    print(f"- model: {model_snapshot}")
    print(f"- srt: {artifact_srt}")
    if artifact_ass:
        print(f"- ass: {artifact_ass}")
    return 0


def _make_smoke_wav() -> Path:
    media_dir = NEXT_ROOT / "smoke_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    wav_path = media_dir / f"next_smoke_{int(time.time())}.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(wav_path),
    ]
    kwargs: dict[str, object] = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True, "encoding": "utf-8", "errors": "ignore"}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to generate smoke wav:\n{result.stdout}")
    return wav_path


def _run_worker(payload: dict[str, object], timeout: int) -> list[dict[str, object]]:
    msg_queue: mp.Queue = mp.Queue()
    cancel_event = mp.Event()
    process = mp.Process(target=run_pipeline_worker, args=(msg_queue, cancel_event, payload))
    process.start()

    started = time.monotonic()
    messages: list[dict[str, object]] = []
    while process.is_alive():
        if time.monotonic() - started > timeout:
            cancel_event.set()
            process.terminate()
            messages.append({"type": MSG_ERROR, "text": f"worker timeout after {timeout}s"})
            break
        try:
            messages.append(msg_queue.get(timeout=0.2))
        except queue.Empty:
            pass

    process.join(timeout=3)
    while True:
        try:
            messages.append(msg_queue.get_nowait())
        except queue.Empty:
            break
    return messages


def _print_message_summary(messages: list[dict[str, object]]) -> None:
    _safe_print(f"message_count={len(messages)}")
    for message in messages:
        msg_type = message.get("type")
        if msg_type in {MSG_STATUS, MSG_PROGRESS, MSG_DONE, MSG_ERROR}:
            _safe_print(f"{msg_type}: {message.get('text')}")
        elif msg_type == MSG_LOG:
            text = str(message.get("text", "")).strip()
            if text:
                _safe_print(f"log: {text[-300:]}")


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def _is_valid_srt(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0 and "-->" in path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def _is_valid_ass(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return path.exists() and path.stat().st_size > 0 and "[Events]" in text and "Dialogue:" in text
    except Exception:
        return False


def _subtitle_payload(path: Path) -> list[str]:
    payload: list[str] = []
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        payload.append(stripped)
    return payload


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
