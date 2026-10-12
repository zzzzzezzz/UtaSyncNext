from __future__ import annotations

"""Run several research-only lyric alignment cases with one shared Whisper model."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.research_lyric_assisted_alignment import (  # noqa: E402
    _extract_window,
    _model_snapshot,
    _normalize,
    _transcribe_probe,
    _write_full_preview,
    _write_preview,
)


GENERIC_PROMPT = "这是现场演唱的日语歌词。请准确转写，不要补写未唱出的内容。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research-only multi-case lyric alignment regression.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig", errors="replace"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest must contain a non-empty cases list")

    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    prepared = [_prepare_case(dict(case), manifest_path.parent, output_root) for case in cases]

    from engine.pipeline_worker import _prepare_engine_runtime

    _prepare_engine_runtime()
    from faster_whisper import WhisperModel

    model_path = _model_snapshot()
    matrix_started = time.time()
    print(f"matrix_model_loading path={model_path}", flush=True)
    model = WhisperModel(str(model_path), device="cuda", compute_type="float16")
    print(f"matrix_model_ready elapsed={time.time() - matrix_started:.2f}s", flush=True)

    results = []
    for index, case in enumerate(prepared, start=1):
        print(f"matrix_case_started index={index}/{len(prepared)} id={case['id']}", flush=True)
        results.append(_run_case(model, case))
        print(
            f"matrix_case_finished id={case['id']} status={results[-1]['status']} "
            f"lyrics_matches={results[-1]['lyrics_probe']['matched_line_count']}/{len(case['expected_lines'])}",
            flush=True,
        )

    report = {
        "schema_version": 1,
        "purpose": "research_only_lyric_alignment_regression_matrix",
        "production_subtitle_changed": False,
        "manifest": str(manifest_path),
        "model": str(model_path),
        "elapsed_seconds": round(time.time() - matrix_started, 2),
        "status": "PASS" if all(result["status"] == "PASS" for result in results) else "FAIL",
        "cases": results,
    }
    report_path = output_root / "lyric_alignment_matrix_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"matrix_done status={report['status']} output={report_path}", flush=True)
    return 0 if report["status"] == "PASS" else 1


def _prepare_case(case: dict[str, object], manifest_dir: Path, output_root: Path) -> dict[str, object]:
    case_id = str(case.get("id") or "").strip()
    if not case_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in case_id):
        raise ValueError(f"invalid case id: {case_id!r}")
    expected_lines = [str(line).strip() for line in case.get("expected_lines") or [] if str(line).strip()]
    if len(expected_lines) < 3:
        raise ValueError(f"{case_id}: at least three expected lines are required")
    start = float(case.get("start") or 0.0)
    end = float(case.get("end") or 0.0)
    if end <= start or end - start > 120:
        raise ValueError(f"{case_id}: window must be between 0 and 120 seconds")

    media = _resolve_path(case.get("media"), manifest_dir)
    lyrics = _resolve_path(case.get("lyrics"), manifest_dir)
    if not media.is_file() or not lyrics.is_file():
        raise FileNotFoundError(f"{case_id}: media or lyrics file does not exist")
    lyrics_text = lyrics.read_text(encoding="utf-8-sig", errors="replace")
    if any(_normalize(line) not in _normalize(lyrics_text) for line in expected_lines):
        raise ValueError(f"{case_id}: expected line is absent from lyrics file")

    source_srt = _optional_path(case.get("source_srt"), manifest_dir)
    diagnostics = _optional_path(case.get("diagnostics"), manifest_dir)
    case_dir = output_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    source_clip = case_dir / "source_window.wav"
    _extract_window(media, source_clip, start, end)
    return {
        **case,
        "id": case_id,
        "media": media,
        "lyrics": lyrics,
        "source_srt": source_srt,
        "diagnostics": diagnostics,
        "source_clip": source_clip,
        "output_dir": case_dir,
        "start": start,
        "end": end,
        "expected_lines": expected_lines,
        "expectations": dict(case.get("expectations") or {}),
    }


def _run_case(model, case: dict[str, object]) -> dict[str, object]:
    case_started = time.time()
    expected_lines = list(case["expected_lines"])
    lyric_prompt = "现场演唱可能包含以下歌词，只转写确实唱出的内容：\n" + "\n".join(expected_lines)
    probes = []
    for label, prompt in (("source_generic", GENERIC_PROMPT), ("source_lyrics", lyric_prompt)):
        probe_started = time.time()
        print(f"matrix_probe_started case={case['id']} label={label}", flush=True)
        probe = _transcribe_probe(
            model,
            Path(case["source_clip"]),
            label=label,
            prompt=prompt,
            offset=float(case["start"]),
            expected_lines=expected_lines,
        )
        probe["elapsed_seconds"] = round(time.time() - probe_started, 2)
        probes.append(probe)
        print(
            f"matrix_probe_finished case={case['id']} label={label} "
            f"matched={probe['matched_line_count']}/{len(expected_lines)} elapsed={probe['elapsed_seconds']}s",
            flush=True,
        )

    lyrics_probe = probes[1]
    output_dir = Path(case["output_dir"])
    source_srt = case.get("source_srt")
    source_hash_before = _sha256(source_srt) if isinstance(source_srt, Path) else ""
    preview_path = output_dir / "lyric_alignment_preview.srt"
    preview_written = _write_preview(preview_path, lyrics_probe, expected_lines)
    full_preview_path = output_dir / "lyric_alignment_full_preview.srt"
    full_preview = _write_full_preview(
        full_preview_path,
        case.get("source_srt"),
        case.get("diagnostics"),
        lyrics_probe,
        expected_lines,
    ) if preview_written else {
        "written": False,
        "replaced_source_cues": [],
        "rejection_reason": "strict_alignment_preview_not_available",
    }

    source_hash_after = _sha256(source_srt) if isinstance(source_srt, Path) else ""
    observed = {
        "lyrics_matches": int(lyrics_probe["matched_line_count"]),
        "preview_written": preview_written,
        "full_preview_written": bool(full_preview["written"]),
        "source_unchanged": source_hash_before == source_hash_after,
    }
    evaluation = evaluate_expectations(observed, dict(case["expectations"]))
    result = {
        "id": case["id"],
        "description": str(case.get("description") or ""),
        "status": "PASS" if evaluation["passed"] else "FAIL",
        "elapsed_seconds": round(time.time() - case_started, 2),
        "media": str(case["media"]),
        "window": {"start": case["start"], "end": case["end"]},
        "expected_lines": expected_lines,
        "source_srt": str(source_srt) if source_srt else "",
        "source_srt_sha256_before": source_hash_before,
        "source_srt_sha256_after": source_hash_after,
        "preview_written": preview_written,
        "preview_path": str(preview_path) if preview_written else "",
        "full_preview_written": bool(full_preview["written"]),
        "full_preview_path": str(full_preview_path) if full_preview["written"] else "",
        "full_preview_rejection_reason": str(full_preview.get("rejection_reason") or ""),
        "blocking_source_cue": full_preview.get("blocking_source_cue"),
        "replaced_source_cues": full_preview.get("replaced_source_cues") or [],
        "expectations": case["expectations"],
        "evaluation": evaluation,
        "generic_probe": probes[0],
        "lyrics_probe": lyrics_probe,
    }
    (output_dir / "case_evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def evaluate_expectations(observed: dict[str, object], expectations: dict[str, object]) -> dict[str, object]:
    failures: list[str] = []
    matches = int(observed.get("lyrics_matches") or 0)
    minimum = expectations.get("minimum_lyrics_matches")
    maximum = expectations.get("maximum_lyrics_matches")
    if minimum is not None and matches < int(minimum):
        failures.append(f"lyrics_matches={matches} is below minimum {int(minimum)}")
    if maximum is not None and matches > int(maximum):
        failures.append(f"lyrics_matches={matches} exceeds maximum {int(maximum)}")
    for key in ("preview_written", "full_preview_written", "source_unchanged"):
        expected_key = f"expect_{key}"
        if expected_key in expectations and bool(observed.get(key)) != bool(expectations[expected_key]):
            failures.append(f"{key}={bool(observed.get(key))} expected {bool(expectations[expected_key])}")
    return {"passed": not failures, "failures": failures}


def _resolve_path(value: object, manifest_dir: Path) -> Path:
    if not str(value or "").strip():
        raise ValueError("required path is empty")
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (manifest_dir / path).resolve()


def _optional_path(value: object, manifest_dir: Path) -> Path | None:
    if not str(value or "").strip():
        return None
    path = _resolve_path(value, manifest_dir)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


if __name__ == "__main__":
    raise SystemExit(main())
