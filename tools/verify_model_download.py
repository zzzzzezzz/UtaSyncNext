from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = NEXT_ROOT.parent
sys.path.insert(0, str(NEXT_ROOT))

from engine.model_downloader import (  # noqa: E402
    MODEL_REPOS,
    MODEL_SOURCES,
    SOURCE_CUSTOM,
    SOURCE_MIRROR,
    SOURCE_OFFICIAL,
    ModelDownloadError,
    _normalize_proxy_url,
    _resolve_endpoint,
    download_model,
    preflight_model_download,
)
from engine.model_registry import MODEL_ORDER, find_complete_snapshot, next_models_root, scan_models  # noqa: E402
from engine.pipeline_worker import _model_roots, _prepare_engine_runtime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify UtaSyncNext model download plumbing.")
    parser.add_argument("--online", action="store_true", help="Run a HuggingFace dry-run preflight without downloading files.")
    parser.add_argument("--download", action="store_true", help="Actually download the selected model into UtaSyncNext/models_faster.")
    parser.add_argument("--load", action="store_true", help="Load the selected model with faster-whisper on CPU int8.")
    parser.add_argument("--model", default="base", choices=MODEL_ORDER, help="Model to verify.")
    parser.add_argument("--source", default=SOURCE_OFFICIAL, choices=list(MODEL_SOURCES.keys()), help="Download source.")
    parser.add_argument("--endpoint", default="", help="Custom endpoint when --source is 自定义源.")
    parser.add_argument("--proxy", default="", help="Optional proxy, for example 127.0.0.1:10808.")
    args = parser.parse_args()

    failures: list[str] = []
    _check_offline_contract(failures)
    _check_registry_temp_cases(failures)

    if args.online:
        _check_online_preflight(args.model, args.source, args.endpoint, args.proxy, failures)
    if args.download:
        _check_real_download(args.model, args.source, args.endpoint, args.proxy, failures)
    if args.load:
        _check_model_load(args.model, failures)

    if failures:
        print("FAIL")
        for item in failures:
            print(f"- {item}")
        return 1

    print("PASS")
    print(f"- Next root: {NEXT_ROOT}")
    print(f"- Model root: {next_models_root()}")
    print(f"- Offline model states: {[(item.name, item.state) for item in scan_models(next_models_root())]}")
    if not args.online and not args.download and not args.load:
        print("- Online dry-run skipped. Add --online to verify remote reachability without downloading models.")
    return 0


def _check_offline_contract(failures: list[str]) -> None:
    expected_root = NEXT_ROOT / "models_faster"
    if next_models_root().resolve() != expected_root.resolve():
        failures.append(f"Next model root mismatch: {next_models_root()} != {expected_root}")

    v3_model_root = REPO_ROOT / "models_faster"
    if next_models_root().resolve() == v3_model_root.resolve():
        failures.append("Next model root resolves to the V3/root models_faster directory.")

    worker_roots = _model_roots()
    if len(worker_roots) != 1 or worker_roots[0][0].resolve() != expected_root.resolve():
        failures.append(f"Worker model roots should contain only Next root, got: {worker_roots}")

    missing_repos = [model for model in MODEL_ORDER if model not in MODEL_REPOS]
    if missing_repos:
        failures.append(f"Missing repo mapping for models: {missing_repos}")

    if _resolve_endpoint(SOURCE_OFFICIAL, "") is not None:
        failures.append("Official source should use the huggingface_hub default endpoint.")
    if _resolve_endpoint(SOURCE_MIRROR, "") != "https://hf-mirror.com":
        failures.append("HF Mirror source does not resolve to https://hf-mirror.com.")
    if _resolve_endpoint(SOURCE_CUSTOM, "https://example.com/hf/") != "https://example.com/hf":
        failures.append("Custom source should trim a trailing slash.")

    for bad_endpoint in ("", "hf-mirror.com"):
        try:
            _resolve_endpoint(SOURCE_CUSTOM, bad_endpoint)
            failures.append(f"Invalid custom endpoint was accepted: {bad_endpoint!r}")
        except ModelDownloadError:
            pass

    if _normalize_proxy_url("") is not None:
        failures.append("Empty proxy should normalize to None.")
    if _normalize_proxy_url("10808") != "http://127.0.0.1:10808":
        failures.append("Port-only proxy should normalize to http://127.0.0.1:10808.")
    if _normalize_proxy_url("127.0.0.1:10808") != "http://127.0.0.1:10808":
        failures.append("Host:port proxy should normalize to http://127.0.0.1:10808.")
    if _normalize_proxy_url("http://127.0.0.1:10808/") != "http://127.0.0.1:10808":
        failures.append("Proxy normalization should trim trailing slash.")


def _check_registry_temp_cases(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="utasyncnext-model-test-") as tmp:
        root = Path(tmp)
        _make_cache_dir(root, "small")
        _make_complete_snapshot(root, "base")
        _make_complete_snapshot(root, "large-v3-turbo")

        states = {item.name: item for item in scan_models(root)}
        if states["small"].state != "未完整":
            failures.append(f"Expected small to be 未完整, got {states['small'].state}")
        if states["base"].state != "完整":
            failures.append(f"Expected base to be 完整, got {states['base'].state}")
        if states["large-v3-turbo"].state != "完整":
            failures.append(f"Expected large-v3-turbo to be 完整, got {states['large-v3-turbo'].state}")
        if states["large-v3"].state != "未安装":
            failures.append("large-v3 was incorrectly matched by a large-v3-turbo cache directory.")


def _make_cache_dir(root: Path, model_name: str) -> Path:
    cache = root / f"models--Systran--faster-whisper-{model_name}"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _make_complete_snapshot(root: Path, model_name: str) -> Path:
    snapshot = _make_cache_dir(root, model_name) / "snapshots" / "fake-snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
    with (snapshot / "model.bin").open("wb") as file:
        file.write(b"0" * (1024 * 1024 + 1))
    return snapshot


def _check_online_preflight(model: str, source: str, endpoint: str, proxy: str, failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="utasyncnext-preflight-") as tmp:
        try:
            result = preflight_model_download(
                model,
                source_name=source,
                custom_endpoint=endpoint,
                proxy_url=proxy,
                cache_dir=Path(tmp),
            )
        except Exception as exc:
            failures.append(f"Online dry-run failed for {model} via {source}: {exc}")
            return

        if result.file_count <= 0:
            failures.append(f"Online dry-run returned no files for {result.repo_id}.")
            return

        size_text = "unknown size" if result.total_size is None else f"{result.total_size / (1024 * 1024):.1f} MB"
        endpoint_text = result.endpoint or "official default"
        print(f"ONLINE PASS: {result.repo_id} via {endpoint_text}, files={result.file_count}, total={size_text}")


def _check_real_download(model: str, source: str, endpoint: str, proxy: str, failures: list[str]) -> None:
    def report(text: str) -> None:
        print(f"DOWNLOAD: {text}")

    try:
        snapshot = download_model(model, source_name=source, custom_endpoint=endpoint, proxy_url=proxy, progress_cb=report)
    except Exception as exc:
        failures.append(f"Real download failed for {model} via {source}: {exc}")
        return

    print(f"DOWNLOAD PASS: {model} -> {snapshot}")


def _check_model_load(model: str, failures: list[str]) -> None:
    snapshot = find_complete_snapshot(model, next_models_root())
    if not snapshot:
        failures.append(f"Cannot load {model}: no complete local snapshot in {next_models_root()}.")
        return

    _prepare_engine_runtime()
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        failures.append(f"Cannot load {model}: faster_whisper is not installed: {exc}")
        return

    try:
        whisper_model = WhisperModel(str(snapshot), device="cpu", compute_type="int8")
        del whisper_model
    except Exception as exc:
        failures.append(f"faster-whisper load failed for {model}: {exc}")
        return

    print(f"LOAD PASS: {model} loaded from {snapshot}")


if __name__ == "__main__":
    raise SystemExit(main())
