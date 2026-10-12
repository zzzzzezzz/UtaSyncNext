from __future__ import annotations

import sys
import tempfile
import json
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.watermark_hallucination_cleanup import apply_watermark_hallucination_cleanup  # noqa: E402
from core.asr_diagnostics import build_asr_diagnostics  # noqa: E402


def main() -> int:
    rows = [
        (0, 4, "歌詞はそのまま残す\n歌词原样保留"),
        (4, 8, "Sound Hodori 사운드 호돌이 サウンドゥ ホドリ"),
        (8, 12, "Instagram & Twitter フォロワー スタートルール"),
        (12, 16, "https://www.instagram.com.twitter.com.jp"),
        (16, 20, "Instagram の話をしたい"),
        (20, 24, "Twitter で告知した"),
    ]
    with tempfile.TemporaryDirectory(prefix="utasyncnext-watermark-cleanup-") as temp_dir:
        path = Path(temp_dir) / "source.srt"
        _write_srt(path, rows)
        diagnostic_rows = [
            {
                "index": index,
                "start": float(start),
                "end": float(end),
                "text": text,
                "avg_logprob": -0.25,
                "no_speech_prob": 0.82 if index in {2, 3, 4} else 0.05,
                "compression_ratio": 1.1,
            }
            for index, (start, end, text) in enumerate(rows, start=1)
        ]
        diagnostic = build_asr_diagnostics(diagnostic_rows, source_srt=str(path))
        diagnostic["opening_cleanup"] = {"policy": "fixture", "removed_count": 1}
        result = apply_watermark_hallucination_cleanup(path, diagnostic)
        text = path.read_text(encoding="utf-8")
        _require(result.changed and result.removed_count == 3, "expected only watermark cues to be removed")
        _require("Sound Hodori" not in text and "フォロワー" not in text and "instagram.com.twitter" not in text, "watermark survived")
        _require("Instagram の話をしたい" in text and "Twitter で告知した" in text, "ordinary social mentions must remain")
        _require("歌詞はそのまま残す\n歌词原样保留" in text, "bilingual line break must remain")
        saved = json.loads(path.with_name("source_识别诊断.json").read_text(encoding="utf-8"))
        _require(saved["evidence_source"] == "faster-whisper", "evidence source should remain full")
        _require(saved["opening_cleanup"]["removed_count"] == 1, "previous cleanup metadata should remain")
        _require(len(saved["segments"]) == 3, "retained evidence count should match retained source segments")
        _require(
            all(item.get("no_speech_prob") is not None for item in saved["segments"]),
            "retained Faster-Whisper confidence evidence must not be erased",
        )
        _require(
            all(item.get("no_speech_prob") is not None for item in saved["suppressed_segments"]),
            "suppressed watermark evidence must retain confidence values",
        )
    print("PASS: watermark cleanup removes only known Sound Hodori/social artifacts")
    return 0


def _write_srt(path: Path, rows: list[tuple[int, int, str]]) -> None:
    parts: list[str] = []
    for index, (start, end, text) in enumerate(rows, start=1):
        parts.extend([str(index), f"00:00:{start:02d},000 --> 00:00:{end:02d},000", text, ""])
    path.write_text("\n".join(parts), encoding="utf-8")


def _require(value: object, message: str) -> None:
    if not value:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
