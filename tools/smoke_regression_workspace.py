from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.regression_workspace import calculate_metrics, compare_timelines, generate_report, inspect_case, parse_srt


SOURCE = """1
00:00:01,000 --> 00:00:02,000
原文一

2
00:00:02,000 --> 00:00:03,000
原文二

3
00:00:03,000 --> 00:00:04,000
原文三
"""

TRANSLATED = """1
00:00:01,000 --> 00:00:02,000
翻译一

3
00:00:03,000 --> 00:00:04,000
深井：翻译三
"""

LYRIC_REFRAINS = """1
00:00:01,000 --> 00:00:02,000
FANTASY FANTASY

2
00:00:02,000 --> 00:00:03,000
Twilight 君だけを見て
"""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="utasync_regression_") as temp_dir:
        temp = Path(temp_dir)
        media = temp / "sample.wav"
        source = temp / "source.srt"
        translated = temp / "translated.srt"
        manifest = temp / "cases.json"
        report_dir = ROOT / "test_workspace" / "reports" / "smoke"
        media.write_bytes(b"RIFF-test")
        source.write_text(SOURCE, encoding="utf-8")
        translated.write_text(TRANSLATED, encoding="utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workspace_name": "smoke",
                    "cases": [
                        {
                            "id": "sample",
                            "media_path": str(media),
                            "acceptance_checks": ["时间轴不丢失", "英文歌名不误报为说话人"],
                            "human_review": {"verdict": "未确认"},
                            "references": [
                                {
                                    "level": "v3_auto",
                                    "source_srt": str(source),
                                    "translated_srt": str(translated),
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        comparison = compare_timelines(source, translated)
        if comparison["source_cues"] != 3 or comparison["translated_cues"] != 2:
            print(f"[FAIL] Timeline comparison mismatch: {comparison}")
            return 1
        metrics = calculate_metrics(parse_srt(TRANSLATED))
        if metrics.suspected_speaker_labels != {"深井:": 1}:
            print(f"[FAIL] Speaker metrics mismatch: {metrics.suspected_speaker_labels}")
            return 1
        if metrics.max_lines_per_cue != 1:
            print(f"[FAIL] Subtitle line metrics mismatch: {metrics.max_lines_per_cue}")
            return 1
        lyric_metrics = calculate_metrics(parse_srt(LYRIC_REFRAINS))
        if lyric_metrics.suspected_speaker_labels:
            print(f"[FAIL] Lyric refrains were reported as speaker labels: {lyric_metrics.suspected_speaker_labels}")
            return 1
        case_report, missing = inspect_case(json.loads(manifest.read_text(encoding="utf-8"))["cases"][0])
        if missing or case_report["acceptance_checks"] != ["时间轴不丢失", "英文歌名不误报为说话人"]:
            print(f"[FAIL] Acceptance metadata mismatch: {case_report}")
            return 1
        if generate_report(manifest, report_dir, strict=True) != 0:
            print("[FAIL] Report generation failed.")
            return 1

    print("[PASS] Regression workspace smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
