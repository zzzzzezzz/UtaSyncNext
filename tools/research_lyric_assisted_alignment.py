from __future__ import annotations

"""Compare conservative lyric-assisted timing probes without touching production SRT."""

import argparse
import difflib
import json
import subprocess
import sys
import time
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research-only lyric-assisted local alignment probe.")
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--lyrics", type=Path, required=True, help="Text file containing the expected lyric lines.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--vocal", type=Path, default=None, help="Optional separated-vocal audio for comparison.")
    parser.add_argument("--source-srt", type=Path, default=None, help="Optional production SRT used only to build a full preview copy.")
    parser.add_argument("--diagnostics", type=Path, default=None, help="Required evidence when a full preview would replace an overlapping cue.")
    parser.add_argument("--line", action="append", default=[], help="Expected line; repeat for each missing lyric line.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.media.is_file() or not args.lyrics.is_file():
        raise FileNotFoundError("media or lyrics file does not exist")
    if args.end <= args.start or args.end - args.start > 120:
        raise ValueError("probe window must be between 0 and 120 seconds")

    expected_lines = [line.strip() for line in args.line if line.strip()]
    if not expected_lines:
        raise ValueError("at least one --line is required")
    lyrics_text = args.lyrics.read_text(encoding="utf-8-sig", errors="replace")
    if any(_normalize(line) not in _normalize(lyrics_text) for line in expected_lines):
        raise ValueError("every expected line must exist in the supplied lyrics file")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_clip = output_dir / "source_window.wav"
    _extract_window(args.media, source_clip, args.start, args.end)
    inputs = [("source", source_clip)]
    if args.vocal and args.vocal.is_file():
        vocal_clip = output_dir / "vocal_window.wav"
        _extract_window(args.vocal, vocal_clip, args.start, args.end)
        inputs.append(("vocal", vocal_clip))

    from engine.pipeline_worker import _prepare_engine_runtime

    _prepare_engine_runtime()
    from faster_whisper import WhisperModel

    model_path = _model_snapshot()
    started = time.time()
    print(f"probe_model_loading path={model_path}", flush=True)
    model = WhisperModel(str(model_path), device="cuda", compute_type="float16")
    generic_prompt = "这是现场演唱的日语歌词。请准确转写，不要补写未唱出的内容。"
    lyric_prompt = "现场演唱可能包含以下歌词，只转写确实唱出的内容：\n" + "\n".join(expected_lines)
    probes: list[dict[str, object]] = []
    for input_label, audio_path in inputs:
        for prompt_label, prompt in (("generic", generic_prompt), ("lyrics", lyric_prompt)):
            probe_started = time.time()
            label = f"{input_label}_{prompt_label}"
            print(f"probe_started label={label}", flush=True)
            probes.append(
                _transcribe_probe(
                    model,
                    audio_path,
                    label=label,
                    prompt=prompt,
                    offset=args.start,
                    expected_lines=expected_lines,
                )
            )
            probes[-1]["elapsed_seconds"] = round(time.time() - probe_started, 2)
            print(
                f"probe_finished label={label} matched={probes[-1]['matched_line_count']}/{len(expected_lines)} "
                f"elapsed={probes[-1]['elapsed_seconds']}s",
                flush=True,
            )

    best = max(probes, key=lambda row: (int(row["matched_line_count"]), float(row["average_match_score"])))
    preview_path = output_dir / "lyric_alignment_preview.srt"
    preview_written = _write_preview(preview_path, best, expected_lines)
    full_preview_path = output_dir / "lyric_alignment_full_preview.srt"
    full_preview = _write_full_preview(
        full_preview_path,
        args.source_srt,
        args.diagnostics,
        best,
        expected_lines,
    ) if preview_written else {
        "written": False,
        "replaced_source_cues": [],
        "rejection_reason": "strict_alignment_preview_not_available",
    }
    report = {
        "schema_version": 1,
        "purpose": "research_only_lyric_assisted_alignment",
        "production_subtitle_changed": False,
        "media": str(args.media.resolve()),
        "vocal": str(args.vocal.resolve()) if args.vocal and args.vocal.is_file() else "",
        "lyrics": str(args.lyrics.resolve()),
        "window": {"start": args.start, "end": args.end},
        "expected_lines": expected_lines,
        "model": str(model_path),
        "elapsed_seconds": round(time.time() - started, 2),
        "best_probe": best["label"],
        "preview_written": preview_written,
        "preview_path": str(preview_path) if preview_written else "",
        "full_preview_written": bool(full_preview["written"]),
        "full_preview_path": str(full_preview_path) if full_preview["written"] else "",
        "replaced_source_cues": full_preview["replaced_source_cues"],
        "full_preview_rejection_reason": str(full_preview.get("rejection_reason") or ""),
        "probes": probes,
    }
    report_path = output_dir / "lyric_alignment_evidence.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"alignment_probe_done best={best['label']} matched={best['matched_line_count']}/{len(expected_lines)} "
        f"preview={preview_written} output={report_path}"
    )
    return 0


def _transcribe_probe(model, audio_path: Path, *, label: str, prompt: str, offset: float, expected_lines: list[str]) -> dict[str, object]:
    segments_iter, _ = model.transcribe(
        str(audio_path),
        language="ja",
        condition_on_previous_text=False,
        beam_size=5,
        temperature=0.0,
        initial_prompt=prompt,
        vad_filter=False,
        word_timestamps=True,
    )
    segments: list[dict[str, object]] = []
    for segment in segments_iter:
        words = [
            {
                "start": round(offset + float(word.start), 3),
                "end": round(offset + float(word.end), 3),
                "word": str(word.word).strip(),
                "probability": round(float(word.probability), 4),
            }
            for word in (segment.words or [])
        ]
        segments.append(
            {
                "start": round(offset + float(segment.start), 3),
                "end": round(offset + float(segment.end), 3),
                "text": str(segment.text).strip(),
                "avg_logprob": round(float(segment.avg_logprob), 4),
                "no_speech_prob": round(float(segment.no_speech_prob), 4),
                "words": words,
            }
        )
    matches = _match_expected_lines(segments, expected_lines)
    scores = [float(item["score"]) for item in matches]
    return {
        "label": label,
        "audio": str(audio_path),
        "prompt_contains_expected_lyrics": label.endswith("_lyrics"),
        "segment_count": len(segments),
        "matched_line_count": sum(score >= 0.62 for score in scores),
        "average_match_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "matches": matches,
        "segments": segments,
    }


def _match_expected_lines(segments: list[dict[str, object]], expected_lines: list[str]) -> list[dict[str, object]]:
    word_matches = _match_expected_lines_by_words(segments, expected_lines)
    if sum(bool(item["accepted"]) for item in word_matches) >= 2:
        return word_matches

    result: list[dict[str, object]] = []
    search_from = 0
    for line in expected_lines:
        best: tuple[float, int, int, str] = (0.0, -1, -1, "")
        for start in range(search_from, len(segments)):
            combined = ""
            for end in range(start, min(len(segments), start + 3)):
                combined += str(segments[end]["text"])
                score = _similarity(combined, line)
                if score > best[0]:
                    best = (score, start, end, combined)
        score, start, end, detected = best
        matched = score >= 0.62 and start >= 0
        result.append(
            {
                "expected": line,
                "detected": detected,
                "score": round(score, 4),
                "start": float(segments[start]["start"]) if matched else None,
                "end": float(segments[end]["end"]) if matched else None,
                "accepted": matched,
            }
        )
        if matched:
            search_from = end + 1
    return result


def _match_expected_lines_by_words(
    segments: list[dict[str, object]],
    expected_lines: list[str],
) -> list[dict[str, object]]:
    words = [
        dict(word)
        for segment in segments
        for word in (segment.get("words") or [])
        if isinstance(word, dict) and _normalize(str(word.get("word") or ""))
    ]
    result: list[dict[str, object]] = []
    search_from = 0
    for line in expected_lines:
        expected_length = len(_normalize(line))
        best: tuple[float, int, int, str] = (0.0, -1, -1, "")
        for start in range(search_from, len(words)):
            combined = ""
            for end in range(start, min(len(words), start + 28)):
                combined += str(words[end].get("word") or "")
                normalized_length = len(_normalize(combined))
                if normalized_length > max(expected_length + 5, int(expected_length * 1.5)):
                    break
                score = _similarity(combined, line)
                if score > best[0]:
                    best = (score, start, end, combined)
        score, start, end, detected = best
        matched = score >= 0.72 and start >= 0
        selected_words = words[start : end + 1] if matched else []
        probabilities = [float(word.get("probability") or 0.0) for word in selected_words]
        result.append(
            {
                "expected": line,
                "detected": detected,
                "score": round(score, 4),
                "start": float(words[start]["start"]) if matched else None,
                "end": float(words[end]["end"]) if matched else None,
                "accepted": matched,
                "evidence": "word_timestamps",
                "average_word_probability": round(sum(probabilities) / len(probabilities), 4) if probabilities else 0.0,
            }
        )
        if matched:
            search_from = end + 1
    return result


def _write_preview(path: Path, probe: dict[str, object], expected_lines: list[str]) -> bool:
    matches = probe.get("matches") if isinstance(probe, dict) else []
    accepted = [item for item in matches if isinstance(item, dict) and item.get("accepted")]
    if len(accepted) != len(expected_lines) or len(accepted) < 3:
        path.unlink(missing_ok=True)
        return False
    if min(float(item["score"]) for item in accepted) < 0.70:
        path.unlink(missing_ok=True)
        return False
    if any(float(current["start"]) < float(previous["end"]) for previous, current in zip(accepted, accepted[1:])):
        path.unlink(missing_ok=True)
        return False
    blocks = [
        f"{index}\n{_timestamp(float(item['start']))} --> {_timestamp(float(item['end']))}\n{item['expected']}"
        for index, item in enumerate(accepted, start=1)
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return True


def _write_full_preview(
    path: Path,
    source_srt_path: Path | None,
    diagnostics_path: Path | None,
    probe: dict[str, object],
    expected_lines: list[str],
) -> dict[str, object]:
    if source_srt_path is None or not source_srt_path.is_file():
        path.unlink(missing_ok=True)
        return {
            "written": False,
            "replaced_source_cues": [],
            "rejection_reason": "source_srt_not_available",
        }

    from core.lyric_gap_detection import SubtitleCue, parse_srt_cues

    matches = [item for item in probe.get("matches") or [] if isinstance(item, dict) and item.get("accepted")]
    if len(matches) != len(expected_lines):
        path.unlink(missing_ok=True)
        return {
            "written": False,
            "replaced_source_cues": [],
            "rejection_reason": "not_all_expected_lines_matched",
        }
    if min(float(item["score"]) for item in matches) < 0.90:
        path.unlink(missing_ok=True)
        return {
            "written": False,
            "replaced_source_cues": [],
            "rejection_reason": "text_match_below_strict_threshold",
        }
    if min(float(item.get("average_word_probability") or 0.0) for item in matches) < 0.75:
        path.unlink(missing_ok=True)
        return {
            "written": False,
            "replaced_source_cues": [],
            "rejection_reason": "word_probability_below_strict_threshold",
        }

    diagnostics: list[dict[str, object]] = []
    if diagnostics_path and diagnostics_path.is_file():
        diagnostic_payload = json.loads(diagnostics_path.read_text(encoding="utf-8-sig", errors="replace"))
        diagnostics = [dict(row) for row in diagnostic_payload.get("segments") or [] if isinstance(row, dict)]

    source_cues = parse_srt_cues(source_srt_path.read_text(encoding="utf-8-sig", errors="replace"))
    aligned_start = min(float(item["start"]) for item in matches)
    aligned_end = max(float(item["end"]) for item in matches)
    retained: list[SubtitleCue] = []
    replaced: list[dict[str, object]] = []
    for cue in source_cues:
        overlaps = cue.start < aligned_end - 0.01 and cue.end > aligned_start + 0.01
        if not overlaps:
            retained.append(cue)
            continue
        evidence = next(
            (
                row for row in diagnostics
                if abs(float(row.get("start") or 0.0) - cue.start) <= 0.1
                and abs(float(row.get("end") or 0.0) - cue.end) <= 0.1
            ),
            None,
        )
        no_speech = float(evidence.get("no_speech_prob") or 0.0) if evidence else 0.0
        lyric_score = max(_similarity(cue.text, line) for line in expected_lines)
        if no_speech >= 0.85 and lyric_score < 0.45:
            replaced.append(
                {
                    "index": cue.index,
                    "start": cue.start,
                    "end": cue.end,
                    "text": cue.text,
                    "no_speech_prob": round(no_speech, 4),
                    "best_expected_lyric_score": round(lyric_score, 4),
                }
            )
            continue
        path.unlink(missing_ok=True)
        return {
            "written": False,
            "replaced_source_cues": [],
            "rejection_reason": "overlapping_source_cue_not_safe_to_replace",
            "blocking_source_cue": {
                "index": cue.index,
                "start": cue.start,
                "end": cue.end,
                "text": cue.text,
                "no_speech_prob": round(no_speech, 4),
                "best_expected_lyric_score": round(lyric_score, 4),
            },
        }

    aligned_cues = [
        SubtitleCue(index=0, start=float(item["start"]), end=float(item["end"]), text=str(item["expected"]))
        for item in matches
    ]
    merged = sorted([*retained, *aligned_cues], key=lambda cue: (cue.start, cue.end, cue.index))
    if any(current.start < previous.end - 0.01 for previous, current in zip(merged, merged[1:])):
        path.unlink(missing_ok=True)
        return {
            "written": False,
            "replaced_source_cues": [],
            "rejection_reason": "merged_preview_contains_overlapping_cues",
        }
    blocks = [
        f"{index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}"
        for index, cue in enumerate(merged, start=1)
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return {"written": True, "replaced_source_cues": replaced, "rejection_reason": ""}


def _extract_window(source: Path, output: Path, start: float, end: float) -> None:
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-to", str(end),
        "-i", str(source.resolve()), "-vn", "-ac", "1", "-ar", "16000", str(output),
    ]
    subprocess.run(command, check=True)


def _model_snapshot() -> Path:
    snapshots = ROOT / "models_faster" / "models--Systran--faster-whisper-large-v2" / "snapshots"
    candidates = sorted(path for path in snapshots.iterdir() if path.is_dir())
    if not candidates:
        raise RuntimeError(f"large-v2 model is missing: {snapshots}")
    return candidates[0]


def _similarity(left: str, right: str) -> float:
    left_normalized = _normalize(left)
    right_normalized = _normalize(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return difflib.SequenceMatcher(None, left_normalized, right_normalized, autojunk=False).ratio()


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds * 1000)))
    hours, total = divmod(total, 3_600_000)
    minutes, total = divmod(total, 60_000)
    secs, milliseconds = divmod(total, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


if __name__ == "__main__":
    raise SystemExit(main())
