from __future__ import annotations

"""Build a review-only preview for a lyric-anchored opening cleanup proposal."""

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.asr_diagnostics import read_srt_segments


_CREDIT_RE = re.compile(r"(?:作詞|作曲|編曲).{0,80}(?:作詞|作曲|編曲)|(?:作詞|作曲|編曲).{0,80}初音", re.I)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a non-production opening cleanup preview.")
    parser.add_argument("srt", type=Path)
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--first-lyric", required=True, help="First confirmed lyric line used only as an anchor.")
    return parser.parse_args()


def _to_srt_time(value: float) -> str:
    milliseconds = int(round(value * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def _write_srt(path: Path, rows: list[dict[str, object]]) -> None:
    lines: list[str] = []
    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                str(index),
                f"{_to_srt_time(float(row['start']))} --> {_to_srt_time(float(row['end']))}",
                str(row["text"]),
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    rows = read_srt_segments(args.srt)
    diagnostics = json.loads(args.diagnostics.read_text(encoding="utf-8"))
    by_index = {int(item["index"]): item for item in diagnostics.get("segments", [])}
    anchor = next((item for item in rows if args.first_lyric.strip() in str(item["text"])), None)
    if anchor is None:
        raise RuntimeError("没有在 SRT 中找到指定的首句歌词锚点，停止生成预览。")

    suppressed: list[dict[str, object]] = []
    retained: list[dict[str, object]] = []
    anchor_start = float(anchor["start"])
    for row in rows:
        evidence = by_index.get(int(row["index"]), {})
        no_speech = evidence.get("no_speech_prob")
        text = str(row["text"])
        before_anchor = float(row["start"]) < anchor_start
        reason = ""
        if before_anchor and isinstance(no_speech, (int, float)) and no_speech >= 0.70:
            reason = "首句确认歌词前，且模型判断该段很可能没有清晰人声"
        elif before_anchor and _CREDIT_RE.search(text):
            reason = "首句确认歌词前的制作名单式误识别"
        if reason:
            suppressed.append({**row, "no_speech_prob": no_speech, "reason": reason})
        else:
            retained.append(row)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    preview_srt = args.output_dir / "opening-cleanup-preview.srt"
    proposal = args.output_dir / "opening-cleanup-proposal.json"
    _write_srt(preview_srt, retained)
    proposal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "purpose": "research_only_opening_cleanup_preview",
                "source_srt": str(args.srt.resolve()),
                "first_confirmed_lyric": args.first_lyric,
                "first_confirmed_lyric_start": anchor_start,
                "proposed_suppression_count": len(suppressed),
                "proposed_suppressions": suppressed,
                "not_integrated_into_production": True,
                "warning": "This preview must be human-reviewed before any production rule is discussed.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"opening_preview_done anchor={anchor_start:.3f}s suppressed={len(suppressed)} "
        f"srt={preview_srt.resolve()} proposal={proposal.resolve()}"
    )


if __name__ == "__main__":
    main()
