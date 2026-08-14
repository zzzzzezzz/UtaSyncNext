from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = NEXT_ROOT.parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from engine.model_downloader import SOURCE_CUSTOM, SOURCE_MIRROR, SOURCE_OFFICIAL  # noqa: E402
from engine.cache_env import apply_next_cache_env, huggingface_cache_root, next_cache_root  # noqa: E402
from engine.locator import find_engine_location  # noqa: E402
from engine.model_registry import next_models_root, scan_models  # noqa: E402


SOURCE_ALIASES = {
    "official": SOURCE_OFFICIAL,
    "mirror": SOURCE_MIRROR,
    "custom": SOURCE_CUSTOM,
    SOURCE_OFFICIAL: SOURCE_OFFICIAL,
    SOURCE_MIRROR: SOURCE_MIRROR,
    SOURCE_CUSTOM: SOURCE_CUSTOM,
}

QUICK_IMPORTS = ["customtkinter", "huggingface_hub", "openai", "httpx"]
ENGINE_IMPORTS = ["faster_whisper"]
LEGACY_PATTERNS = ["_load_v3", "spec_from_file_location", "Cannot load V3", "importlib.util"]
SCAN_SKIP_DIRS = {".venv", ".engine", ".wheelhouse", "__pycache__", "models_faster", "output", "smoke_media", "test_logs"}


@dataclass
class CheckResult:
    name: str
    status: str
    seconds: float
    command: list[str] | None = None
    detail: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="UtaSyncNext systematic verification runner.")
    parser.add_argument(
        "--profile",
        choices=["health", "quick", "network", "model", "pipeline", "full", "download"],
        default="quick",
        help="health is fastest and local. quick adds translator smoke tests. download requires --confirm-download.",
    )
    parser.add_argument("--model", default="small", help="Model used by network/model/pipeline/download profiles.")
    parser.add_argument("--source", default="official", choices=list(SOURCE_ALIASES.keys()), help="Download source alias.")
    parser.add_argument("--endpoint", default="", help="Custom endpoint for --source custom.")
    parser.add_argument("--proxy", default="", help="Optional proxy, e.g. 10808 or 127.0.0.1:10808.")
    parser.add_argument("--media", default="", help="Optional real media path for pipeline profile.")
    parser.add_argument("--mode", choices=["live", "speech"], default="live", help="Pipeline acoustic mode.")
    parser.add_argument("--demucs", action="store_true", help="Enable Demucs in pipeline profile.")
    parser.add_argument("--ass", action="store_true", help="Generate ASS in pipeline profile.")
    parser.add_argument("--timeout", type=int, default=600, help="Pipeline timeout in seconds.")
    parser.add_argument("--confirm-download", action="store_true", help="Required for profile=download.")
    parser.add_argument("--json-report", default="", help="Optional path to write a JSON report.")
    args = parser.parse_args()

    json_report = Path(args.json_report).expanduser().resolve() if args.json_report else None
    os.chdir(NEXT_ROOT)
    results: list[CheckResult] = []

    _add(results, "cache-env", _check_cache_env)
    _add(results, "environment", _check_environment)
    _add(results, "compile", _compile_next_sources)
    _add(results, "legacy-scan", _scan_legacy_wrappers)
    _add(results, "audio-cache-policy", _check_audio_cache_policy)
    _add(results, "demucs-setting-policy", _check_demucs_setting_policy)
    _add(results, "pipeline-cache-fingerprint", _check_pipeline_cache_fingerprint)
    _add(results, "pipeline-preview", _check_pipeline_preview)
    _run_script(results, "model-offline", ["tools/verify_model_download.py"])
    _run_script(results, "translation-guardrails", ["tools/smoke_translation_guardrails.py"])
    _run_script(results, "subtitle-density", ["tools/smoke_subtitle_density.py"])
    _run_script(results, "regression-workspace", ["tools/smoke_regression_workspace.py"])
    if args.profile != "health":
        _run_script(results, "translator-bilingual", ["tools/smoke_translator.py", "--mode", "bilingual"])
        _run_script(results, "translator-chinese-only", ["tools/smoke_translator.py", "--mode", "chinese_only"])
        _add(results, "ui-imports", _check_ui_imports)

    if args.profile in {"network", "full"}:
        _run_model_verify(results, "model-network-dry-run", args, ["--online"])

    if args.profile in {"model", "full"}:
        _run_model_verify(results, "model-load", args, ["--load"])

    if args.profile in {"pipeline", "full"}:
        _run_pipeline(results, args)

    if args.profile == "download":
        if not args.confirm_download:
            results.append(
                CheckResult(
                    name="model-real-download",
                    status="SKIP",
                    seconds=0.0,
                    detail="Real downloads are disabled unless --confirm-download is provided.",
                )
            )
        else:
            _run_model_verify(results, "model-real-download", args, ["--download"])

    _print_summary(results)
    if json_report:
        json_report.parent.mkdir(parents=True, exist_ok=True)
        json_report.write_text(
            json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return 1 if any(result.status == "FAIL" for result in results) else 0


def _add(results: list[CheckResult], name: str, func) -> None:
    started = time.monotonic()
    try:
        detail = func()
        status = "PASS"
    except AssertionError as exc:
        detail = str(exc)
        status = "FAIL"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        status = "FAIL"
    results.append(CheckResult(name=name, status=status, seconds=time.monotonic() - started, detail=detail or ""))


def _run_script(results: list[CheckResult], name: str, args: list[str], timeout: int = 120) -> None:
    _run_command(results, name, [sys.executable, *args], timeout=timeout)


def _run_model_verify(results: list[CheckResult], name: str, args, extra: list[str]) -> None:
    source = SOURCE_ALIASES[args.source]
    command = ["tools/verify_model_download.py", "--model", args.model, "--source", source, *extra]
    if args.endpoint:
        command.extend(["--endpoint", args.endpoint])
    if args.proxy:
        command.extend(["--proxy", args.proxy])
    timeout = 3600 if "--download" in extra else 180
    _run_script(results, name, command, timeout=timeout)


def _run_pipeline(results: list[CheckResult], args) -> None:
    command = [
        "tools/smoke_full_pipeline.py",
        "--model",
        args.model,
        "--mode",
        args.mode,
        "--timeout",
        str(args.timeout),
    ]
    if args.media:
        command.extend(["--media", args.media])
    if args.demucs:
        command.append("--demucs")
    if args.ass:
        command.append("--ass")
    _run_script(results, "pipeline-smoke", command, timeout=args.timeout + 60)


def _run_command(results: list[CheckResult], name: str, command: list[str], timeout: int) -> None:
    started = time.monotonic()
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            command,
            cwd=NEXT_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        status = "PASS" if completed.returncode == 0 else "FAIL"
        detail = _tail(completed.stdout)
    except subprocess.TimeoutExpired as exc:
        status = "FAIL"
        detail = f"Timed out after {timeout}s.\n{_tail(exc.stdout or '')}"
    results.append(
        CheckResult(
            name=name,
            status=status,
            seconds=time.monotonic() - started,
            command=_display_command(command),
            detail=detail,
        )
    )


def _check_environment() -> str:
    missing = [name for name in QUICK_IMPORTS if importlib.util.find_spec(name) is None]
    if missing:
        raise AssertionError(f"Missing required packages: {', '.join(missing)}")

    engine_location = find_engine_location()
    model_states = [(item.name, item.state) for item in scan_models(next_models_root())]
    env_hint = "OK"
    executable_parts = {part.lower() for part in Path(sys.executable).parts}
    if ".venv" not in executable_parts or "utasyncnext" not in executable_parts:
        env_hint = f"WARNING: runner is not using UtaSyncNext .venv: {sys.executable}"
    return (
        f"{env_hint}; engine_source={engine_location.source_label or '(none)'}; "
        f"engine_root={engine_location.root}; engine_missing={list(engine_location.missing_modules)}; "
        f"engine_legacy={engine_location.is_legacy}; models={model_states}"
    )


def _check_cache_env() -> str:
    values = apply_next_cache_env()
    expected_hf = str(huggingface_cache_root())
    expected_cache = str(next_cache_root())
    if values["HF_HOME"] != expected_hf:
        raise AssertionError(f"HF_HOME should point to Next cache: {values['HF_HOME']}")
    if values["XDG_CACHE_HOME"] != expected_cache:
        raise AssertionError(f"XDG_CACHE_HOME should point to Next cache: {values['XDG_CACHE_HOME']}")
    if "Users\\Administrator\\.cache" in values["HF_HOME"]:
        raise AssertionError("HF_HOME still points to the user profile cache")
    return f"HF_HOME={values['HF_HOME']}; HUGGINGFACE_HUB_CACHE={values['HUGGINGFACE_HUB_CACHE']}"


def _compile_next_sources() -> str:
    targets = ["app.py", "controllers", "core", "engine", "ui", "utils", "tools"]
    command = [sys.executable, "-m", "compileall", "-q", *targets]
    completed = subprocess.run(
        command,
        cwd=NEXT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise AssertionError(_tail(completed.stdout))
    return "compiled app/controllers/core/engine/ui/utils/tools"


def _scan_legacy_wrappers() -> str:
    hits: list[str] = []
    for root, dirnames, filenames in os.walk(NEXT_ROOT):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in SCAN_SKIP_DIRS]
        root_path = Path(root)
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = root_path / filename
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in LEGACY_PATTERNS:
                if pattern in text:
                    hits.append(f"{path.relative_to(NEXT_ROOT)}: {pattern}")
    if hits:
        raise AssertionError("Legacy wrapper patterns found:\n" + "\n".join(hits))
    return "no dynamic legacy wrapper patterns found"


def _check_ui_imports() -> str:
    imports = [
        "ui.main_window",
        "ui.sidebar",
        "ui.workspace",
        "ui.dialogs",
        "ui.gallery",
        "controllers.task_controller",
        "engine.model_downloader",
        "core.generator",
        "core.ass_maker",
        "core.translator",
    ]
    for module_name in imports:
        __import__(module_name)
    return "imported " + ", ".join(imports)


def _check_audio_cache_policy() -> str:
    from core.generator import LiveSubtitleGenerator

    with tempfile.TemporaryDirectory(prefix="utasyncnext-cache-policy-") as temp_dir:
        generator = LiveSubtitleGenerator("cache_policy_fixture.mp4", output_dir=temp_dir)
        audio_path = Path(generator.audio_path)
        vocal_path = Path(generator.final_vocal_path)
        audio_path.parent.mkdir(parents=True, exist_ok=True)

        audio_path.write_bytes(b"source-audio-cache")
        Path(generator.strategy_flag_path).write_text("demucs", encoding="utf-8")
        if not generator._can_reuse_vocal_cache("skip"):
            raise AssertionError("skip strategy should reuse source audio cache independently of strategy flag")
        if Path(generator._cache_path_for_strategy("skip")) != audio_path:
            raise AssertionError("skip strategy should map to source audio cache")

        if generator._can_reuse_vocal_cache("demucs"):
            raise AssertionError("demucs strategy should not be reusable before vocal cache exists")
        vocal_path.write_bytes(b"demucs-vocal-cache")
        if not generator._can_reuse_vocal_cache("demucs"):
            raise AssertionError("demucs strategy should reuse Demucs vocal cache")
        if Path(generator._cache_path_for_strategy("demucs")) != vocal_path:
            raise AssertionError("demucs strategy should map to vocal cache")

        generator.vocal_path = str(vocal_path)
        generator.cleanup_temp_files()
        if not audio_path.exists() or not vocal_path.exists():
            raise AssertionError("cleanup should keep both source and Demucs vocal caches")

    return "source and Demucs caches are independent and survive cleanup"


def _check_demucs_setting_policy() -> str:
    from engine.pipeline_worker import _normalize_pipeline_settings

    _, _, _, skip_fast, _ = _normalize_pipeline_settings({"use_demucs": "先跳过 Demucs (极速/推荐先出轴)"})
    _, _, _, skip_legacy, _ = _normalize_pipeline_settings({"use_demucs": "强制跳过 (极速/仅限无伴奏访谈)"})
    _, _, _, skip_enabled, _ = _normalize_pipeline_settings({"use_demucs": "开启 Demucs 人声分离 (高质量/很慢)"})
    _, _, _, skip_speech, _ = _normalize_pipeline_settings(
        {"audio_type": "访谈 / 播客", "use_demucs": "开启 Demucs 人声分离 (高质量/很慢)"}
    )
    if not skip_fast or not skip_legacy:
        raise AssertionError("fast/legacy skip Demucs options should skip separation")
    if skip_enabled:
        raise AssertionError("explicit Demucs high quality option should enable separation")
    if not skip_speech:
        raise AssertionError("speech mode should always skip Demucs")
    return "Demucs defaults favor fast skip while explicit high-quality mode remains available"


def _check_pipeline_cache_fingerprint() -> str:
    from engine.pipeline_worker import _file_content_hash, _hash_payload, _media_fingerprint, _try_reuse_asr

    with tempfile.TemporaryDirectory(prefix="utasyncnext-cache-fingerprint-") as temp_dir:
        root = Path(temp_dir)
        media = root / "same_name.wav"
        media.write_bytes(b"first media payload")
        first_media = _media_fingerprint(media)
        media.write_bytes(b"second media payload")
        second_media = _media_fingerprint(media)
        if first_media["edge_md5"] == second_media["edge_md5"]:
            raise AssertionError("media fingerprint should change when file content changes")

        srt = root / "sample.srt"
        hash_path = root / "sample.hash"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        first_srt_hash = _file_content_hash(srt)
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nchanged\n", encoding="utf-8")
        second_srt_hash = _file_content_hash(srt)
        if first_srt_hash == second_srt_hash:
            raise AssertionError("source SRT content hash should change when text changes")

        asr_a = _hash_payload({"media": first_media, "model_snapshot": "a", "skip_demucs": True})
        asr_b = _hash_payload({"media": second_media, "model_snapshot": "a", "skip_demucs": True})
        asr_c = _hash_payload({"media": second_media, "model_snapshot": "b", "skip_demucs": True})
        if len({asr_a, asr_b, asr_c}) != 3:
            raise AssertionError("ASR cache hash should include media and model snapshot")

        hash_path.write_text(asr_b, encoding="utf-8")
        if not _try_reuse_asr(srt, hash_path, asr_b):
            raise AssertionError("ASR cache should be reusable when SRT and hash both match")
        if _try_reuse_asr(srt, hash_path, asr_c):
            raise AssertionError("ASR cache should not be reused when hash differs")

    return "media, model, and SRT content changes invalidate pipeline caches"


def _check_pipeline_preview() -> str:
    from engine.pipeline_worker import build_pipeline_preview

    missing_media = build_pipeline_preview({"media_path": "", "settings": {}})
    if missing_media.get("ok") or not missing_media.get("lines"):
        raise AssertionError("preview should return a user-facing warning when media is missing")

    with tempfile.TemporaryDirectory(prefix="utasyncnext-preview-") as temp_dir:
        media = Path(temp_dir) / "preview.wav"
        media.write_bytes(b"preview audio")
        preview = build_pipeline_preview({"media_path": str(media), "settings": {"model_size": "__missing__"}})
        if preview.get("ok"):
            raise AssertionError("preview should not be ok when the requested model is missing")
        if not any("Whisper" in str(line) or "预检" in str(line) for line in preview.get("lines", [])):
            raise AssertionError("preview should include useful route/model information")

    return "pipeline preview produces safe user-facing route summaries"


def _tail(text: str, limit: int = 3500) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return "...<trimmed>...\n" + text[-limit:]


def _display_command(command: list[str]) -> list[str]:
    if command and Path(command[0]).resolve() == Path(sys.executable).resolve():
        return ["python", *command[1:]]
    return command


def _print_summary(results: list[CheckResult]) -> None:
    _safe_print("\nUtaSyncNext Verification Summary")
    _safe_print("=" * 34)
    for result in results:
        _safe_print(f"[{result.status}] {result.name} ({result.seconds:.1f}s)")
        if result.command:
            _safe_print("  cmd: " + " ".join(result.command))
        if result.detail:
            for line in result.detail.splitlines()[:12]:
                _safe_print(f"  {line}")
            if len(result.detail.splitlines()) > 12:
                _safe_print("  ...")
    _safe_print("=" * 34)


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


if __name__ == "__main__":
    raise SystemExit(main())
