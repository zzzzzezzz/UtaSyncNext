from __future__ import annotations

"""Standalone timing probe for difficult lyric passages.

This tool intentionally does not change the production ASR pipeline.  It asks
the same local Faster-Whisper model for word timestamps, then stores raw
evidence for human comparison before any subtitle splitting policy is changed.
"""

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Faster-Whisper word-timing evidence.")
    parser.add_argument("audio", type=Path, help="Short local audio excerpt.")
    parser.add_argument("output", type=Path, help="JSON evidence output path.")
    parser.add_argument("--offset", type=float, default=0.0, help="Original video offset in seconds.")
    parser.add_argument("--label", default="untitled", help="Human-readable probe label.")
    parser.add_argument("--prompt", default="这是现场演唱的日语歌词。请准确转写，不要补写未唱出的内容。")
    return parser.parse_args()


def _model_snapshot() -> Path:
    snapshots = ROOT / "models_faster" / "models--Systran--faster-whisper-large-v2" / "snapshots"
    candidates = sorted(path for path in snapshots.iterdir() if path.is_dir())
    if not candidates:
        raise RuntimeError(f"没有找到 Next 本地 large-v2 模型：{snapshots}")
    return candidates[0]


def main() -> None:
    args = _parse_args()
    if not args.audio.is_file():
        raise FileNotFoundError(args.audio)

    from engine.pipeline_worker import _prepare_engine_runtime

    _prepare_engine_runtime()
    from faster_whisper import WhisperModel

    started = time.time()
    model_path = _model_snapshot()
    model = WhisperModel(str(model_path), device="cuda", compute_type="float16")
    segments, info = model.transcribe(
        str(args.audio),
        language="ja",
        condition_on_previous_text=False,
        beam_size=5,
        initial_prompt=args.prompt,
        vad_filter=False,
        word_timestamps=True,
    )

    rows: list[dict[str, object]] = []
    word_count = 0
    for segment in segments:
        words = []
        for word in segment.words or []:
            word_count += 1
            words.append(
                {
                    "start": round(args.offset + float(word.start), 3),
                    "end": round(args.offset + float(word.end), 3),
                    "word": str(word.word),
                    "probability": round(float(word.probability), 4),
                }
            )
        rows.append(
            {
                "start": round(args.offset + float(segment.start), 3),
                "end": round(args.offset + float(segment.end), 3),
                "text": str(segment.text).strip(),
                "avg_logprob": round(float(segment.avg_logprob), 4),
                "no_speech_prob": round(float(segment.no_speech_prob), 4),
                "compression_ratio": round(float(segment.compression_ratio), 4),
                "words": words,
            }
        )

    report = {
        "schema_version": 1,
        "purpose": "research_only_word_timestamps",
        "label": args.label,
        "audio": str(args.audio.resolve()),
        "video_offset_seconds": args.offset,
        "model": "large-v2 / cuda float16",
        "duration_seconds": round(float(info.duration), 3),
        "elapsed_seconds": round(time.time() - started, 2),
        "segment_count": len(rows),
        "word_count": word_count,
        "segments": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"timing_probe_done label={args.label} segments={len(rows)} words={word_count} "
        f"elapsed={report['elapsed_seconds']}s output={args.output.resolve()}"
    )


if __name__ == "__main__":
    main()
