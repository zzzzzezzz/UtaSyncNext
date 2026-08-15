from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from utils.ffmpeg_tools import safe_run


DEFAULT_MANIFEST = NEXT_ROOT / "test_workspace" / "cases.local.json"
DEFAULT_REPORT_DIR = NEXT_ROOT / "test_workspace" / "reports"
TIME_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)
COLON_LABEL_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9_. -]{1,24}|[\u3400-\u9fff\u3040-\u30ffー]{2,8})\s*[:：]"
)
BARE_LABEL_RE = re.compile(
    r"^(?P<label>[A-Z][A-Za-z]{2,20}|[\u3400-\u9fff]{2,4})\s+"
)
KNOWN_SPEAKER_LABELS = {
    "fukai",
    "higuchi",
    "tomitake",
    "yanyan",
    "yanyang",
    "向井",
    "深井",
    "樋口",
    "神谷",
    "とみたけ",
    "ヤンヤン",
}


@dataclass(frozen=True)
class SubtitleBlock:
    time_line: str
    start_ms: int
    end_ms: int
    text_lines: tuple[str, ...]


@dataclass(frozen=True)
class SubtitleMetrics:
    cue_count: int
    first_start: str
    last_end: str
    overlaps: int
    non_monotonic: int
    max_duration_seconds: float
    max_text_units: int
    max_lines_per_cue: int
    suspected_speaker_labels: dict[str, int]


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a local UtaSync regression workspace.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report", help="Generate JSON and Markdown evidence reports.")
    report_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Local case manifest path.")
    report_parser.add_argument("--output-dir", default=str(DEFAULT_REPORT_DIR), help="Report directory inside Next.")
    report_parser.add_argument("--strict", action="store_true", help="Fail when required media or references are missing.")

    args = parser.parse_args()
    if args.command == "report":
        return generate_report(Path(args.manifest), Path(args.output_dir), strict=args.strict)
    return 2


def generate_report(manifest_path: Path, output_dir: Path, *, strict: bool = False) -> int:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.exists():
        print(f"[FAIL] Manifest not found: {manifest_path}")
        return 2

    data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    _validate_manifest(data)
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at,
        "manifest": str(manifest_path),
        "workspace_name": data.get("workspace_name", "UtaSyncNext regression lab"),
        "cases": [],
    }

    missing_required = 0
    for case in data["cases"]:
        case_report, case_missing = inspect_case(case)
        report["cases"].append(case_report)
        missing_required += case_missing

    output_dir = _resolve_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"regression-report-{stamp}.json"
    markdown_path = output_dir / f"regression-report-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")

    print("[PASS] Regression evidence report generated.")
    print(f"- cases: {len(report['cases'])}")
    print(f"- missing required paths: {missing_required}")
    print(f"- json: {json_path}")
    print(f"- markdown: {markdown_path}")
    return 1 if strict and missing_required else 0


def inspect_case(case: dict[str, Any]) -> tuple[dict[str, Any], int]:
    media_path = Path(str(case.get("media_path", ""))).expanduser()
    lyrics_raw = str(case.get("lyrics_path", "")).strip()
    lyrics_path = Path(lyrics_raw).expanduser() if lyrics_raw else None
    missing = 0

    media_info = inspect_file(media_path, media=True)
    if not media_info["exists"]:
        missing += 1
    lyrics_info = inspect_file(lyrics_path) if lyrics_path else None
    if lyrics_path and not lyrics_info["exists"]:
        missing += 1

    references: list[dict[str, Any]] = []
    for reference in case.get("references", []):
        source_path = _optional_path(reference.get("source_srt"))
        translated_path = _optional_path(reference.get("translated_srt"))
        source_info = inspect_subtitle(source_path) if source_path else None
        translated_info = inspect_subtitle(translated_path) if translated_path else None
        if source_path and not source_info["file"]["exists"]:
            missing += 1
        if translated_path and not translated_info["file"]["exists"]:
            missing += 1

        comparison = None
        if source_info and translated_info and source_info["file"]["exists"] and translated_info["file"]["exists"]:
            comparison = compare_timelines(source_path, translated_path)
        references.append(
            {
                "level": reference.get("level", "unknown"),
                "source": source_info,
                "translated": translated_info,
                "timeline_comparison": comparison,
            }
        )

    return (
        {
            "id": case["id"],
            "title": case.get("title", case["id"]),
            "tier": case.get("tier", "anchor"),
            "status": case.get("status", "needs_review"),
            "tags": case.get("tags", []),
            "settings": case.get("settings", {}),
            "clips": case.get("clips", []),
            "acceptance_checks": case.get("acceptance_checks", []),
            "human_review": case.get("human_review", {}),
            "notes": case.get("notes", ""),
            "media": media_info,
            "lyrics": lyrics_info,
            "references": references,
        },
        missing,
    )


def inspect_file(path: Path | None, *, media: bool = False) -> dict[str, Any]:
    if path is None:
        return {"path": "", "exists": False}
    resolved = path.resolve()
    if not resolved.is_file():
        return {"path": str(resolved), "exists": False}
    stat = resolved.stat()
    result: dict[str, Any] = {
        "path": str(resolved),
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
    }
    result["fingerprint_kind"] = "sampled_sha256" if media else "sha256"
    result["fingerprint"] = _sampled_hash(resolved) if media else _full_hash(resolved)
    if media:
        result["probe"] = _probe_media(resolved)
    return result


def _probe_media(path: Path) -> dict[str, Any]:
    try:
        result = safe_run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(path),
            ],
            timeout=20,
        )
        if result.returncode != 0:
            return {"status": "failed"}
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams", []) if isinstance(data, dict) else []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        duration = float((data.get("format") or {}).get("duration") or 0.0)
        return {
            "status": "ok",
            "duration_seconds": round(duration, 3),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "video_codec": str(video.get("codec_name") or ""),
            "audio_codec": str(audio.get("codec_name") or ""),
        }
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {"status": "unavailable"}


def inspect_subtitle(path: Path) -> dict[str, Any]:
    file_info = inspect_file(path)
    if not file_info["exists"]:
        return {"file": file_info, "metrics": None}
    blocks = parse_srt(path.read_text(encoding="utf-8-sig", errors="ignore"))
    return {"file": file_info, "metrics": asdict(calculate_metrics(blocks))}


def parse_srt(content: str) -> list[SubtitleBlock]:
    raw_blocks = [part.strip() for part in re.split(r"\n\s*\n", content.strip()) if part.strip()]
    blocks: list[SubtitleBlock] = []
    for raw_block in raw_blocks:
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        time_index = next((idx for idx, line in enumerate(lines) if TIME_RE.search(line)), -1)
        if time_index < 0:
            continue
        match = TIME_RE.search(lines[time_index])
        if not match:
            continue
        text_lines = tuple(lines[time_index + 1 :])
        if not text_lines:
            continue
        blocks.append(
            SubtitleBlock(
                time_line=match.group(0),
                start_ms=_timestamp_ms(match.group("start")),
                end_ms=_timestamp_ms(match.group("end")),
                text_lines=text_lines,
            )
        )
    return blocks


def calculate_metrics(blocks: list[SubtitleBlock]) -> SubtitleMetrics:
    overlaps = 0
    non_monotonic = 0
    previous_start = -1
    previous_end = -1
    max_duration = 0
    max_units = 0
    max_lines = 0
    label_candidates: list[str] = []
    for block in blocks:
        if previous_end >= 0 and block.start_ms < previous_end:
            overlaps += 1
        if previous_start >= 0 and block.start_ms < previous_start:
            non_monotonic += 1
        previous_start = block.start_ms
        previous_end = block.end_ms
        max_duration = max(max_duration, block.end_ms - block.start_ms)
        max_units = max(max_units, *(_display_units(line) for line in block.text_lines))
        max_lines = max(max_lines, len(block.text_lines))
        label_candidates.extend(_speaker_candidates(block.text_lines))

    counts = Counter(label_candidates)
    suspicious = {name: count for name, count in sorted(counts.items()) if count >= 1}
    return SubtitleMetrics(
        cue_count=len(blocks),
        first_start=_format_ms(blocks[0].start_ms) if blocks else "",
        last_end=_format_ms(blocks[-1].end_ms) if blocks else "",
        overlaps=overlaps,
        non_monotonic=non_monotonic,
        max_duration_seconds=round(max_duration / 1000, 3),
        max_text_units=max_units,
        max_lines_per_cue=max_lines,
        suspected_speaker_labels=suspicious,
    )


def compare_timelines(source_path: Path, translated_path: Path) -> dict[str, Any]:
    source = parse_srt(source_path.read_text(encoding="utf-8-sig", errors="ignore"))
    translated = parse_srt(translated_path.read_text(encoding="utf-8-sig", errors="ignore"))
    source_times = [block.time_line for block in source]
    translated_times = [block.time_line for block in translated]
    positional_matches = sum(left == right for left, right in zip(source_times, translated_times))
    source_set = set(source_times)
    translated_set = set(translated_times)
    denominator = max(1, len(source_times))
    return {
        "source_cues": len(source_times),
        "translated_cues": len(translated_times),
        "cue_delta": len(translated_times) - len(source_times),
        "positional_timeline_matches": positional_matches,
        "positional_match_ratio": round(positional_matches / denominator, 4),
        "missing_source_timelines": len(source_set - translated_set),
        "added_timelines": len(translated_set - source_set),
    }


def _speaker_candidates(lines: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for line in lines:
        colon = COLON_LABEL_RE.match(line)
        if colon:
            found.append(colon.group("label").strip() + ":")
            continue
        bare = BARE_LABEL_RE.match(line)
        if bare and bare.group("label").strip().casefold() in KNOWN_SPEAKER_LABELS:
            found.append(bare.group("label").strip())
    return found


def _validate_manifest(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema_version; expected 1.")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifest must contain at least one case.")
    ids: set[str] = set()
    for case in cases:
        case_id = str(case.get("id", "")).strip()
        if not case_id:
            raise ValueError("Every case needs a non-empty id.")
        if case_id in ids:
            raise ValueError(f"Duplicate case id: {case_id}")
        ids.add(case_id)
        if not str(case.get("media_path", "")).strip():
            raise ValueError(f"Case {case_id} needs media_path.")
        checks = case.get("acceptance_checks", [])
        if checks and (
            not isinstance(checks, list)
            or any(not isinstance(item, str) or not item.strip() for item in checks)
        ):
            raise ValueError(f"Case {case_id} acceptance_checks must be a list of non-empty strings.")


def _resolve_output_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(NEXT_ROOT)
    except ValueError as exc:
        raise ValueError(f"Report output must stay inside UtaSyncNext: {resolved}") from exc
    return resolved


def _optional_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    return Path(text).expanduser() if text else None


def _timestamp_ms(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, milliseconds = rest.split(",")
    return (((int(hours) * 60 + int(minutes)) * 60) + int(seconds)) * 1000 + int(milliseconds)


def _format_ms(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _display_units(text: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in text.strip())


def _full_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sampled_hash(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if size > 1024 * 1024:
            handle.seek(max(0, size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    return digest.hexdigest()


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['workspace_name']}",
        "",
        f"生成时间：`{report['generated_at']}`",
        "",
        "> 这是客观证据报告，不会自动判定翻译和打轴质量。只有人工确认稿才能作为质量基准。",
        "",
    ]
    for case in report["cases"]:
        media = case["media"]
        lines.extend(
            [
                f"## {case['title']}",
                "",
                f"- Case: `{case['id']}`",
                f"- 层级/状态：`{case['tier']}` / `{case['status']}`",
                f"- 标签：{', '.join(case['tags']) or '-'}",
                f"- 素材：`{media['path']}`（{'已找到' if media['exists'] else '缺失'}）",
                f"- 设置：`{json.dumps(case['settings'], ensure_ascii=False, sort_keys=True)}`",
            ]
        )
        probe = media.get("probe") or {}
        if probe.get("status") == "ok":
            duration = _format_duration_seconds(float(probe.get("duration_seconds") or 0))
            width = int(probe.get("width") or 0)
            height = int(probe.get("height") or 0)
            frame = f"{width}x{height}" if width and height else "纯音频"
            codecs = "/".join(value for value in (probe.get("video_codec"), probe.get("audio_codec")) if value)
            lines.append(f"- 媒体信息：`{duration}`，`{frame}`，`{codecs or '未知编码'}`")
        if case["clips"]:
            for clip in case["clips"]:
                lines.append(
                    f"- 测试片段：{clip.get('label', '片段')} `{clip.get('start', '')}` + `{clip.get('duration', '')}`"
                )
        else:
            lines.append("- 测试片段：尚未选择")
        if case["acceptance_checks"]:
            lines.append("- 人工验收重点：" + "；".join(case["acceptance_checks"]))
        review = case["human_review"]
        if review:
            lines.append(
                f"- 人工结论：`{review.get('verdict', '未确认')}`"
                + (f"，{review.get('notes', '')}" if review.get("notes") else "")
            )
        for reference in case["references"]:
            lines.append(f"- 参考级别：`{reference['level']}`")
            source_metrics = (reference.get("source") or {}).get("metrics")
            translated_metrics = (reference.get("translated") or {}).get("metrics")
            if source_metrics:
                lines.append(f"- 原文指标：{_format_subtitle_metrics(source_metrics)}")
            if translated_metrics:
                lines.append(f"- 输出指标：{_format_subtitle_metrics(translated_metrics)}")
            comparison = reference.get("timeline_comparison")
            if comparison:
                lines.append(
                    "- 时间轴："
                    f"{comparison['source_cues']} -> {comparison['translated_cues']} 条；"
                    f"同位置一致 {comparison['positional_match_ratio']:.1%}；"
                    f"缺少 {comparison['missing_source_timelines']}，新增 {comparison['added_timelines']}"
                )
            translated = reference.get("translated")
            if translated and translated.get("metrics"):
                labels = translated["metrics"]["suspected_speaker_labels"]
                if labels:
                    lines.append(f"- 疑似说话人前缀：`{json.dumps(labels, ensure_ascii=False)}`")
        if case["notes"]:
            lines.append(f"- 说明：{case['notes']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_duration_seconds(value: float) -> str:
    total = max(0, int(round(value)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_subtitle_metrics(metrics: dict[str, Any]) -> str:
    return (
        f"{int(metrics.get('cue_count') or 0)} 条，"
        f"最长 {float(metrics.get('max_duration_seconds') or 0):.2f} 秒，"
        f"最长单行 {int(metrics.get('max_text_units') or 0)} 显示单位，"
        f"每条最多 {int(metrics.get('max_lines_per_cue') or 0)} 行，"
        f"重叠 {int(metrics.get('overlaps') or 0)}"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(2)
