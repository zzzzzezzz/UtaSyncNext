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
    _bilingual_line_counts,
    _should_retry_bilingual_coverage,
    _strip_known_speaker_labels,
    _suspected_opening_lyric_gaps,
    _suspected_lyric_sequence_issues,
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

    incomplete_bilingual = """1
00:00:01,000 --> 00:00:02,000
原文一

2
00:00:02,000 --> 00:00:03,000
原文二
"""
    complete_bilingual = incomplete_bilingual.replace("原文一", "原文一\n翻译一").replace(
        "原文二", "原文二\n翻译二"
    )
    if _bilingual_line_counts(incomplete_bilingual) != (0, 2):
        print("[FAIL] Missing Chinese lines were not detected.")
        return 1
    if _bilingual_line_counts(complete_bilingual) != (2, 2):
        print("[FAIL] Complete bilingual lines were not recognized.")
        return 1
    if not _should_retry_bilingual_coverage(11, 12, already_retried=False):
        print("[FAIL] A single missing translation did not trigger one local retry.")
        return 1
    if _should_retry_bilingual_coverage(11, 12, already_retried=True):
        print("[FAIL] A mostly complete batch would retry forever after the local retry.")
        return 1
    if not _should_retry_bilingual_coverage(8, 12, already_retried=True):
        print("[FAIL] A substantially incomplete batch was accepted after one retry.")
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
    lyric_prompt = translator._build_prompt(
        "1\n00:00:01,000 --> 00:00:02,000\n握り\n\n2\n00:00:02,000 --> 00:00:03,000\nなおした",
        "",
        "少し湿った手を握りなおした",
    )
    if (
        "前一轴写完整句、后一轴又重复句尾" not in lyric_prompt
        or "不要提前套用下一句歌词" not in lyric_prompt
        or "严禁为了逐行套歌词而占用后续时间轴" not in lyric_prompt
    ):
        print("[FAIL] Prompt does not contain the lyric sequence alignment policy.")
        return 1
    if "歌词顺序对齐" in prompt:
        print("[FAIL] Lyric sequence rules leaked into blind translation mode.")
        return 1

    lyric_source = [
        SrtBlock(1, "00:00:01,000 --> 00:00:02,000", ("君が笑うその香り",)),
        SrtBlock(2, "00:00:02,000 --> 00:00:03,000", ("触れたいから",)),
    ]
    lyric_overlap = """1
00:00:01,000 --> 00:00:02,000
君が笑う その顔に触れたいから
因为想触碰你微笑的脸

2
00:00:02,000 --> 00:00:03,000
触れたいから
想要触碰
"""
    overlap_issues = _suspected_lyric_sequence_issues(lyric_overlap, lyric_source)
    if "1->2" not in overlap_issues:
        print(f"[FAIL] Adjacent lyric tail duplication was not detected: {overlap_issues}")
        return 1

    shifted_source = [
        SrtBlock(7, "00:01:11,000 --> 00:01:16,500", ("ねえ もしも 全て忘れられたなら",)),
        SrtBlock(8, "00:01:16,500 --> 00:01:22,500", ("泣かないで 生きることも楽になるの",)),
        SrtBlock(9, "00:01:22,500 --> 00:01:26,740", ("でもそんなことできないから",)),
        SrtBlock(10, "00:01:26,740 --> 00:01:30,500", ("もう何も見せないでよ",)),
        SrtBlock(11, "00:01:32,500 --> 00:01:38,200", ("君にどれだけ近づいても",)),
    ]
    shifted_output = """7
00:01:11,000 --> 00:01:16,500
ねえ もしも
呐 如果

8
00:01:16,500 --> 00:01:22,500
全て忘れられたなら
能够忘记一切的话

9
00:01:22,500 --> 00:01:26,740
泣かないで
不要哭泣

10
00:01:26,740 --> 00:01:30,500
生きることも楽になるの
生活就会变得轻松吗

11
00:01:32,500 --> 00:01:38,200
でもそんなことできないから
但这种事我做不到
"""
    displacement_issues = _suspected_lyric_sequence_issues(shifted_output, shifted_source)
    if not {"7=>8", "8=>9", "8=>10", "9=>11"}.issubset(set(displacement_issues)):
        print(f"[FAIL] Cross-cue lyric displacement was not detected: {displacement_issues}")
        return 1

    actual_refrain = [
        SrtBlock(1, "00:00:01,000 --> 00:00:02,000", ("エンドロールいらないよ",)),
        SrtBlock(2, "00:00:02,000 --> 00:00:03,000", ("エンドロールいらないよ",)),
    ]
    refrain_output = lyric_overlap.replace("君が笑う その顔に触れたいから", "エンドロールいらないよ").replace(
        "触れたいから", "エンドロールいらないよ"
    )
    if _suspected_lyric_sequence_issues(refrain_output, actual_refrain):
        print("[FAIL] A refrain already present in the source was treated as model duplication.")
        return 1

    short_tail_source = [
        SrtBlock(61, "00:05:27,600 --> 00:05:31,600", ("また美しい理由",)),
        SrtBlock(62, "00:05:31,600 --> 00:05:38,600", ("現実と偽善分が",)),
        SrtBlock(63, "00:05:39,600 --> 00:05:41,600", ("疲れたよ",)),
    ]
    short_tail_output = """61
00:05:27,600 --> 00:05:31,600
また美しい言い訳
又一次美丽的借口

62
00:05:31,600 --> 00:05:38,600
現実逃避 全部が疲れたよ
逃避现实 一切都让我疲惫不堪

63
00:05:39,600 --> 00:05:41,600
疲れたよ
好累啊
"""
    if _suspected_lyric_sequence_issues(short_tail_output, short_tail_source):
        print("[FAIL] A corrected lyric containing a short adjacent tail was rejected.")
        return 1

    opening_reference = """おしゃかしゃま - RADWIMPS
词：野田洋次郎
曲：野田洋次郎
カラスが増えたから殺します
さらに猿が増えたから減らします
でもパンダは減ったから増やします
けど人類は増えても増やします"""
    opening_shift = """1
00:01:25,000 --> 00:01:27,000
さらに猿が増えたから減らします
因为猴子又增加了所以要减少

2
00:01:27,000 --> 00:01:29,000
でもパンダは減ったから増やします
但是熊猫减少了就要增加
"""
    opening_issues = _suspected_opening_lyric_gaps(opening_shift, opening_reference)
    if len(opening_issues) != 1 or opening_issues[0]["time"] != "00:01:25,000":
        print(f"[FAIL] Opening reference lyric gap was not located: {opening_issues}")
        return 1
    opening_complete = opening_shift.replace(
        "さらに猿が増えたから減らします",
        "カラスが増えたから殺します\n因为乌鸦增加了所以要杀掉\n\n3\n"
        "00:01:27,000 --> 00:01:29,000\nさらに猿が増えたから減らします",
        1,
    )
    if _suspected_opening_lyric_gaps(opening_complete, opening_reference):
        print("[FAIL] Complete opening lyrics were treated as a gap.")
        return 1

    repeated_reference = """色彩 - yama
词：くじら
曲：くじら
じっと機微を見逃さないで
きっと大人はそれらしくするので
かかりきりになるんだ
関心はまだ僕ら
真珠の欠片だって無価値
そこに物語がなければ
からくりのなかで廻る僕ら命だ
じっと日々を見逃さないで
きっと大人はそれらしくするので
かかりきりになるんだ
関心はまだ僕ら
真珠のかけらだって無価値
そこに物語がなければ
からくりの中で廻る僕ら命だ
"""
    repeated_output = """1
00:00:26,000 --> 00:00:28,000
じっと日々を見逃さないで
日复一日不要错过

2
00:00:28,000 --> 00:00:31,000
きっと大人はそれらしくするので
大人一定会装出大人的样子

3
00:00:31,000 --> 00:00:33,000
かかりきりになるんだ
会变得专注其中

4
00:00:33,000 --> 00:00:35,000
関心はまだ僕ら
我们仍然在意

5
00:00:35,000 --> 00:00:37,000
真珠のかけらだって無価値
即使是珍珠碎片也没有价值

6
00:00:37,000 --> 00:00:39,000
そこに物語がなければ
如果那里没有故事

7
00:00:39,000 --> 00:00:41,000
からくりの中で廻る僕ら命だ
我们是转动于机关中的生命
"""
    if _suspected_opening_lyric_gaps(repeated_output, repeated_reference):
        print("[FAIL] A later repeated refrain was treated as a false opening gap.")
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
            "translation_bilingual_coverage_retries": 1,
            "translation_speaker_label_warnings": 2,
            "translation_speaker_labels_removed": 3,
            "translation_lyric_alignment_warnings": 1,
            "translation_lyric_gap_warnings": 1,
            "translation_model": "deepseek-ai/DeepSeek-V3",
        }
    )
    if (
        "已保留该处原文" not in summary
        or "批次缺少中文" not in summary
        or "疑似说话人冠名" not in summary
        or "已移除 3 处高置信角色标签" not in summary
        or "相邻歌词疑似重复" not in summary
        or "参考歌词开场缺口" not in summary
        or "DeepSeek-V3" not in summary
    ):
        print("[FAIL] User-facing summary does not explain translation guardrail results.")
        return 1

    print("[PASS] Translation timeline and speaker-label guardrails passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
