from __future__ import annotations

import json
import sys
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.lyric_gap_detection import detect_opening_lyric_gaps  # noqa: E402
from core.lyric_gap_repair import automatic_lyric_gap_rescan_rejection_reason  # noqa: E402


def main() -> int:
    manifest_path = NEXT_ROOT / "test_workspace" / "cases.local.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checked = 0
    candidates = 0
    automatic_candidates = 0

    for case in manifest.get("cases", []):
        lyrics_value = str(case.get("lyrics_path") or "").strip()
        if not lyrics_value:
            continue
        lyrics_path = Path(lyrics_value)
        if not lyrics_path.exists():
            continue
        lyrics = lyrics_path.read_text(encoding="utf-8-sig", errors="replace")

        for reference in case.get("references", []):
            source_value = str(reference.get("source_srt") or "").strip()
            source_path = Path(source_value)
            if not source_value or not source_path.exists():
                continue
            checked += 1
            found = detect_opening_lyric_gaps(
                source_path.read_text(encoding="utf-8-sig", errors="replace"),
                lyrics,
            )
            candidates += len(found)
            automatic_candidates += sum(
                not automatic_lyric_gap_rescan_rejection_reason(item.__dict__)
                for item in found
            )
            print(
                f"[{len(found)}] {case.get('id')} / {reference.get('level', '')}"
            )
            for item in found:
                print(
                    "    "
                    f"{item.section_title or '(未命名歌曲)'} | cue={item.cue_index} | "
                    f"gap={item.gap_seconds:.3f}s | "
                    f"auto={'yes' if not automatic_lyric_gap_rescan_rejection_reason(item.__dict__) else 'no'} | "
                    f"{item.detected_text}"
                )

    print(f"checked={checked} candidates={candidates} automatic_candidates={automatic_candidates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
