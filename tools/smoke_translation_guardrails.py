from __future__ import annotations

import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.translator import (
    SrtBlock,
    SubtitleTranslator,
    _strip_known_speaker_labels,
    _suspected_speaker_labels,
)
from ui.log_formatter import format_run_summary


def main() -> int:
    translator = SubtitleTranslator.__new__(SubtitleTranslator)
    translator.output_mode = "bilingual"
    translator.failure_lock = threading.Lock()
    translator.alignment_fallbacks = 0

    original = [
        SrtBlock(1, "00:00:01,000 --> 00:00:02,000", ("原文一",)),
        SrtBlock(2, "00:00:02,000 --> 00:00:03,000", ("原文二",)),
        SrtBlock(3, "00:00:03,000 --> 00:00:04,000", ("原文三",)),
    ]
    translated_with_middle_missing = """1
00:00:01,000 --> 00:00:02,000
校正一
翻译一

3
00:00:03,000 --> 00:00:04,000
校正三
翻译三
"""
    restored = translator._restore_timestamps(translated_with_middle_missing, original)
    blocks = restored.split("\n\n")
    if "翻译一" not in blocks[0] or "原文二" not in blocks[1] or "翻译三" not in blocks[2]:
        print("[FAIL] Missing middle translation shifted later subtitles.")
        print(restored)
        return 1
    if translator.alignment_fallbacks != 1:
        print(f"[FAIL] Expected one alignment fallback, got {translator.alignment_fallbacks}.")
        return 1

    labels = _suspected_speaker_labels(
        "1\n00:00:01,000 --> 00:00:02,000\n深井：今日は\n深井 今天\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nFukai そうです\nFukai 是的"
    )
    if labels != ["Fukai", "深井"]:
        print(f"[FAIL] Speaker label detection mismatch: {labels}")
        return 1

    natural_names = _suspected_speaker_labels(
        "1\n00:00:01,000 --> 00:00:02,000\n神谷です。お願いします。\n我是神谷，请多关照。"
    )
    if natural_names:
        print(f"[FAIL] Natural name usage was treated as a speaker label: {natural_names}")
        return 1

    lyric_refrains = _suspected_speaker_labels(
        "1\n00:00:01,000 --> 00:00:02,000\nFANTASY FANTASY\n幻想 幻想\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nTwilight 君だけを見て\n暮色中只注视着你"
    )
    if lyric_refrains:
        print(f"[FAIL] English lyric refrains were treated as speaker labels: {lyric_refrains}")
        return 1

    cleaned, removed = _strip_known_speaker_labels(
        "1\n00:00:01,000 --> 00:00:02,000\nヤンヤン 結構印象あります 深井 マジで？\n"
        "2\n00:00:02,000 --> 00:00:03,000\n樋口：三次啊w\n"
        "3\n00:00:03,000 --> 00:00:04,000\n神谷です。神谷 さんもいます。\n"
        "4\n00:00:04,000 --> 00:00:05,000\nFANTASY FANTASY\nTwilight 君だけを見て"
    )
    if removed != 3 or any(label in cleaned for label in ["ヤンヤン", "深井", "樋口"]):
        print(f"[FAIL] High-confidence speaker labels were not cleaned safely: removed={removed}\n{cleaned}")
        return 1
    if "神谷です" not in cleaned or "神谷 さん" not in cleaned or "FANTASY" not in cleaned or "Twilight" not in cleaned:
        print(f"[FAIL] Natural names or lyric words were removed by speaker cleanup:\n{cleaned}")
        return 1

    translator.layout_profile = "wide_16_9"
    translator.ch_max_units = 64
    translator.jp_max_units = 84
    translator.context_title = "ordinary interview"
    prompt = translator._build_prompt("1\n00:00:01,000 --> 00:00:02,000\n神谷です", "", "")
    if (
        "不要删除句意中的姓名" not in prompt
        or "不要作为字幕冠名前缀输出" not in prompt
        or "英文歌词、歌名和反复句" not in prompt
    ):
        print("[FAIL] Prompt does not contain the scoped speaker-label policy.")
        return 1

    if "不要猜姓氏" not in prompt or "yamaさん" in prompt:
        print("[FAIL] Generic proper-name policy is missing or leaked the yama-specific rule.")
        return 1

    translator.context_title = "#170yamaびこリフレイン"
    yama_prompt = translator._build_prompt(
        "1\n00:00:01,000 --> 00:00:02,000\n山さんの口癖ってなんですか",
        "",
        "",
    )
    if "山さん」必须校正并保留为「yamaさん" not in yama_prompt or "山田" not in yama_prompt:
        print("[FAIL] yama-specific proper-name policy is missing.")
        return 1

    summary = format_run_summary(
        {
            "translation_requested": True,
            "translation_alignment_fallbacks": 1,
            "translation_speaker_label_warnings": 2,
            "translation_speaker_labels_removed": 3,
            "translation_model": "deepseek-ai/DeepSeek-V3",
        }
    )
    if (
        "已保留该处原文" not in summary
        or "疑似说话人冠名" not in summary
        or "已移除 3 处高置信角色标签" not in summary
        or "DeepSeek-V3" not in summary
    ):
        print("[FAIL] User-facing summary does not explain translation guardrail results.")
        return 1

    print("[PASS] Translation timeline and speaker-label guardrails passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
