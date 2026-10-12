from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
TIME_RE = re.compile(
    r"^(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})$"
)


@dataclass(frozen=True)
class SrtCue:
    start_ms: int
    end_ms: int
    text_lines: tuple[str, ...]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a traceable regression reference from an approved source timeline."
    )
    parser.add_argument("--source-input", required=True)
    parser.add_argument("--translated-input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-offset-seconds", type=float, default=0.0)
    parser.add_argument(
        "--drop-source-text",
        action="append",
        default=[],
        help="Drop only a source cue whose complete normalized text exactly matches this value.",
    )
    args = parser.parse_args()

    source_path = Path(args.source_input).expanduser().resolve()
    translated_path = Path(args.translated_input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    _require_inside_next(output_dir)
    if not source_path.is_file() or not translated_path.is_file():
        missing = [str(path) for path in (source_path, translated_path) if not path.is_file()]
        raise FileNotFoundError(f"Missing input: {', '.join(missing)}")

    source_cues = parse_srt(source_path)
    translated_cues = parse_srt(translated_path)
    drop_texts = {_normalize_text(value) for value in args.drop_source_text if value.strip()}
    kept_source = [cue for cue in source_cues if _normalize_cue_text(cue) not in drop_texts]
    removed_source = [cue for cue in source_cues if _normalize_cue_text(cue) in drop_texts]

    offset_ms = round(args.source_offset_seconds * 1000)
    allowed = Counter((cue.start_ms + offset_ms, cue.end_ms + offset_ms) for cue in kept_source)
    kept_translated: list[SrtCue] = []
    for cue in translated_cues:
        key = (cue.start_ms, cue.end_ms)
        if allowed[key] > 0:
            kept_translated.append(cue)
            allowed[key] -= 1

    missing_timelines = [key for key, count in allowed.items() for _ in range(count)]
    if missing_timelines:
        preview = ", ".join(_format_timeline(*item) for item in missing_timelines[:5])
        raise ValueError(f"Translated input is missing {len(missing_timelines)} approved timelines: {preview}")
    if len(kept_source) != len(kept_translated):
        raise ValueError(
            f"Cue count mismatch after filtering: source={len(kept_source)}, translated={len(kept_translated)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    source_output = output_dir / "source.srt"
    translated_output = output_dir / "translated.srt"
    source_output.write_text(render_srt(kept_source), encoding="utf-8")
    translated_output.write_text(render_srt(kept_translated), encoding="utf-8")

    provenance = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "operation": "filter translated cues by approved source timelines",
        "source_offset_seconds": args.source_offset_seconds,
        "drop_source_text_exact": sorted(drop_texts),
        "inputs": {
            "source": _file_record(source_path, len(source_cues)),
            "translated": _file_record(translated_path, len(translated_cues)),
        },
        "outputs": {
            "source": _file_record(source_output, len(kept_source)),
            "translated": _file_record(translated_output, len(kept_translated)),
        },
        "removed_source_cues": [
            {
                "timeline": _format_timeline(cue.start_ms, cue.end_ms),
                "text": "\n".join(cue.text_lines),
            }
            for cue in removed_source
        ],
        "filtered_translated_cues": len(translated_cues) - len(kept_translated),
        "quality_claim": "none; this file records a mechanical reference transformation",
    }
    (output_dir / "REFERENCE.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("[PASS] Regression reference materialized.")
    print(f"- source: {len(source_cues)} -> {len(kept_source)}")
    print(f"- translated: {len(translated_cues)} -> {len(kept_translated)}")
    print(f"- output: {output_dir}")
    return 0


def parse_srt(path: Path) -> list[SrtCue]:
    chunks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip())
    cues: list[SrtCue] = []
    for chunk in chunks:
        lines = [line.rstrip("\r") for line in chunk.splitlines()]
        if not lines:
            continue
        time_index = next((index for index, line in enumerate(lines) if TIME_RE.match(line.strip())), None)
        if time_index is None:
            continue
        match = TIME_RE.match(lines[time_index].strip())
        assert match is not None
        text_lines = tuple(line for line in lines[time_index + 1 :] if line.strip())
        cues.append(
            SrtCue(
                start_ms=_parse_timestamp(match.group("start")),
                end_ms=_parse_timestamp(match.group("end")),
                text_lines=text_lines,
            )
        )
    if not cues:
        raise ValueError(f"No SRT cues found: {path}")
    return cues


def render_srt(cues: list[SrtCue]) -> str:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    _format_timeline(cue.start_ms, cue.end_ms),
                    *cue.text_lines,
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def _parse_timestamp(value: str) -> int:
    hours, minutes, remainder = value.split(":")
    seconds, milliseconds = remainder.split(",")
    return (((int(hours) * 60 + int(minutes)) * 60) + int(seconds)) * 1000 + int(milliseconds)


def _format_timestamp(value_ms: int) -> str:
    hours, remainder = divmod(value_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _format_timeline(start_ms: int, end_ms: int) -> str:
    return f"{_format_timestamp(start_ms)} --> {_format_timestamp(end_ms)}"


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _normalize_cue_text(cue: SrtCue) -> str:
    return _normalize_text(" ".join(cue.text_lines))


def _file_record(path: Path, cue_count: int) -> dict[str, object]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "cue_count": cue_count,
    }


def _require_inside_next(path: Path) -> None:
    try:
        path.relative_to(NEXT_ROOT)
    except ValueError as exc:
        raise ValueError(f"Output directory must stay inside UtaSyncNext: {path}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
