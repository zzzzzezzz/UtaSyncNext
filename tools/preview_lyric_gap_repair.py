from __future__ import annotations

import argparse
import sys
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.lyric_gap_repair import write_lyric_gap_repair_preview  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a review-only lyric gap repair preview.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--rescan", required=True)
    parser.add_argument("--candidate-report", required=True)
    parser.add_argument("--offset-seconds", type=float, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output, evidence, preview = write_lyric_gap_repair_preview(
        args.source,
        args.rescan,
        args.candidate_report,
        args.output,
        rescan_offset_seconds=args.offset_seconds,
    )
    print(f"inserted={len(preview.insertions)} source={preview.source_cue_count} preview={preview.preview_cue_count}")
    print(f"preview={output.resolve()}")
    print(f"evidence={evidence.resolve()}")
    return 0 if preview.insertions else 2


if __name__ == "__main__":
    raise SystemExit(main())
