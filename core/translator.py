from __future__ import annotations

import concurrent.futures
import hashlib
import inspect
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ProgressCallback = Callable[[float, str], None]
PROMPT_POLICY_VERSION = "next-v10-lyric-overlap-retry"
API_REQUEST_TIMEOUT_SECONDS = 180.0
SPEAKER_COLON_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9_. -]{1,24}|[\u3400-\u9fff\u3040-\u30ffー]{2,8})\s*[:：]"
)
SPEAKER_BARE_RE = re.compile(r"^(?P<label>[A-Z][A-Za-z]{2,20}|[\u3400-\u9fff]{2,4})\s+")
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
KNOWN_SPEAKER_TOKEN_RE = re.compile(
    rf"(?P<prefix>^|\s)(?P<label>{'|'.join(re.escape(label) for label in sorted(KNOWN_SPEAKER_LABELS, key=len, reverse=True))})"
    r"(?P<separator>\s*[:：]\s*|\s+)",
    re.IGNORECASE,
)
SPEAKER_HONORIFIC_RE = re.compile(r"^(?:さん|くん|ちゃん|氏|先生|様)")


@dataclass(frozen=True)
class SrtBlock:
    index: int
    time_line: str
    text_lines: tuple[str, ...]
    index_explicit: bool = True

    def to_srt(self) -> str:
        return f"{self.index}\n{self.time_line}\n" + "\n".join(self.text_lines)


class SubtitleTranslator:
    """
    Next-native OpenAI-compatible subtitle translator.

    The translator intentionally preserves the original SRT timeline after the
    model returns text. This keeps timing stable even when the model reformats,
    omits, or renumbers a batch.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        primary_model: str,
        reasoning_model: str | None = None,
        srt_path: str = "",
        reference_txt_path: str | None = None,
        output_mode: str = "bilingual",
        proxy_url: str = "",
        layout_profile: str = "wide_16_9",
        ch_max_units: int = 64,
        jp_max_units: int = 84,
        context_title: str = "",
    ) -> None:
        from openai import OpenAI

        self.api_key = api_key
        self.base_url = base_url
        self.primary_model = primary_model
        self.reasoning_model = reasoning_model or primary_model
        self.srt_path = Path(srt_path)
        self.reference_txt_path = Path(reference_txt_path) if reference_txt_path else None
        self.output_mode = _normalize_output_mode(output_mode)
        self.layout_profile = str(layout_profile or "wide_16_9")
        self.ch_max_units = max(20, int(ch_max_units or 64))
        self.jp_max_units = max(28, int(jp_max_units or 84))
        self.context_title = str(context_title or "").strip()
        self.max_workers = 2
        self.cancel_event = None

        base_path = self.srt_path.with_suffix("")
        suffix = "纯中文字幕" if self.output_mode == "chinese_only" else "双语字幕"
        self.output_srt_path = str(base_path.with_name(f"{base_path.name}_{suffix}.srt"))
        self.cache_file = base_path.with_name(f"{base_path.name}_翻译缓存.json")
        self.api_trace_file = base_path.with_name(f"{base_path.name}_api_trace.json")
        self.cache_lock = threading.Lock()
        self.failure_lock = threading.Lock()
        self.api_trace_lock = threading.Lock()
        self.translation_cache = self._load_cache()
        self.failed_batches = 0
        self.total_batches = 0
        self.cache_hits = 0
        self.api_calls = 0
        self.alignment_fallbacks = 0
        self.speaker_label_warnings = 0
        self.speaker_labels_removed = 0
        self.lyric_alignment_warnings = 0
        self.active_model = self.primary_model
        self.returned_models: set[str] = set()
        self.api_trace: dict[str, object] = {}
        self.api_trace_announced = False

        client_kwargs = {
            "api_key": api_key,
            "base_url": base_url,
            "timeout": API_REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        }
        http_client = _build_http_client(proxy_url)
        if http_client is not None:
            client_kwargs["http_client"] = http_client

        self.client = OpenAI(**client_kwargs)

    def run(self, cancel_event=None, progress_cb: ProgressCallback | None = None) -> str | None:
        self.cancel_event = cancel_event
        self.failed_batches = 0
        self.total_batches = 0
        self.cache_hits = 0
        self.api_calls = 0
        self.alignment_fallbacks = 0
        self.speaker_label_warnings = 0
        self.speaker_labels_removed = 0
        self.lyric_alignment_warnings = 0
        self.returned_models.clear()
        print("\n[Next 翻译阶段] 启动独立大模型翻译器...")

        try:
            start_time = time.time()
            reference_lyrics = self.read_reference_lyrics()
            self.active_model = self.primary_model if reference_lyrics else self.reasoning_model
            self._start_api_trace(self.active_model, bool(reference_lyrics))
            blocks = self.parse_srt()
            if not blocks:
                raise RuntimeError("输入 SRT 为空或格式无效。")

            final_srt_content = self.translate_blocks(blocks, reference_lyrics, progress_cb=progress_cb)
            if self.failed_batches:
                print(f"[警告] 翻译批次失败 {self.failed_batches}/{self.total_batches}。")
            if self.total_batches and self.failed_batches >= self.total_batches:
                raise RuntimeError("所有翻译批次均失败，未生成有效翻译结果。")
            final_srt_content = self._restore_timestamps(final_srt_content, blocks)
            final_srt_content = self._reindex_srt(final_srt_content)
            if not final_srt_content.strip():
                raise RuntimeError("翻译结果为空。")

            output_path = Path(self.output_srt_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(final_srt_content + "\n", encoding="utf-8")

            elapsed = int(time.time() - start_time)
            print(f"[完成] Next 翻译完成，耗时 {elapsed} 秒。")
            print(f"[输出] 翻译字幕已保存: {output_path}")
            self._finish_api_trace("completed")
            if progress_cb:
                progress_cb(0.90, "翻译排版完成")
            return str(output_path)
        except Exception as exc:
            self._finish_api_trace("failed", str(exc))
            if "用户中断" not in str(exc):
                print(f"\n[错误] 翻译阶段失败: {exc}")
                print("[建议] 检查 API Key、Base URL、模型名或代理设置后重试；已完成批次会保留缓存。")
            return None

    def read_reference_lyrics(self) -> str:
        if self.reference_txt_path and self.reference_txt_path.exists():
            try:
                content = self.reference_txt_path.read_text(encoding="utf-8").strip()
                if content:
                    print(f"   -> 已加载参考歌词: {self.reference_txt_path.name}")
                    return content
            except Exception as exc:
                print(f"   -> 参考歌词读取失败: {exc}")

        print("   -> 未检测到参考歌词，将使用盲翻模式。")
        return ""

    def parse_srt(self) -> list[SrtBlock]:
        content = self.srt_path.read_text(encoding="utf-8-sig", errors="ignore").strip()
        raw_blocks = [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]
        parsed: list[SrtBlock] = []

        for fallback_index, raw_block in enumerate(raw_blocks, start=1):
            lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
            time_idx = next((i for i, line in enumerate(lines) if "-->" in line), -1)
            if time_idx < 0:
                continue

            index = fallback_index
            index_explicit = False
            if time_idx > 0:
                digits = re.sub(r"\D", "", lines[0])
                if digits:
                    index = int(digits)
                    index_explicit = True

            text_lines = tuple(lines[time_idx + 1 :])
            if text_lines:
                parsed.append(
                    SrtBlock(
                        index=index,
                        time_line=lines[time_idx],
                        text_lines=text_lines,
                        index_explicit=index_explicit,
                    )
                )

        return parsed

    def translate_blocks(
        self,
        blocks: list[SrtBlock],
        reference_lyrics: str = "",
        progress_cb: ProgressCallback | None = None,
    ) -> str:
        batch_size = 12
        batches = [blocks[i : i + batch_size] for i in range(0, len(blocks), batch_size)]
        total_batches = len(batches)
        self.total_batches = total_batches
        self.failed_batches = 0
        if total_batches == 0:
            return ""

        active_model = self.active_model
        print(f"   -> 本次翻译模型: {active_model}")
        print(f"   -> 启动翻译队列，并发 {self.max_workers}，共 {total_batches} 个批次。")

        results: list[tuple[int, str]] = []
        completed = 0
        last_logged_pct = -1

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_batch = {}
            for idx, batch in enumerate(batches):
                prev_context = _blocks_to_srt(blocks[max(0, idx * batch_size - 2) : idx * batch_size])
                future = executor.submit(
                    self.translate_single_batch,
                    batch,
                    prev_context,
                    idx,
                    total_batches,
                    reference_lyrics,
                    active_model,
                )
                future_to_batch[future] = idx

            for future in concurrent.futures.as_completed(future_to_batch):
                if self.cancel_event and self.cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise RuntimeError("用户中断：已清空翻译任务队列")

                batch_idx, translated_text = future.result()
                results.append((batch_idx, translated_text))
                completed += 1
                pct = int(completed / total_batches * 100)

                if progress_cb:
                    progress_cb(0.60 + completed / total_batches * 0.30, f"AI 语境翻译中... {pct}%")
                if pct - last_logged_pct >= 5 or last_logged_pct == -1:
                    print(f"      [翻译进度] 批次完成 -> {pct}%")
                    last_logged_pct = pct

        results.sort(key=lambda item: item[0])
        return "\n\n".join(text for _, text in results)

    def translate_single_batch(
        self,
        batch: list[SrtBlock],
        prev_context: str,
        batch_idx: int,
        total_batches: int,
        reference_lyrics: str,
        target_model: str,
    ) -> tuple[int, str]:
        if self.cancel_event and self.cancel_event.is_set():
            raise RuntimeError("用户中断：翻译线程已终止")

        cache_key = _batch_cache_key(
            batch,
            self.output_mode,
            target_model,
            self.layout_profile,
            self.ch_max_units,
            self.jp_max_units,
            reference_lyrics,
            self.context_title,
        )
        cached = self.translation_cache.get(cache_key)
        original_batch_srt = _blocks_to_srt(batch)
        if isinstance(cached, str) and cached.strip() and not _same_subtitle_payload(cached, original_batch_srt):
            self.cache_hits += 1
            self._record_cache_hit(target_model, batch_idx)
            return batch_idx, cached

        prompt_batch_srt, source_labels_removed = _strip_known_speaker_labels(original_batch_srt)
        if source_labels_removed:
            with self.failure_lock:
                self.speaker_labels_removed += source_labels_removed
            print(
                f"      批次 {batch_idx + 1}/{total_batches} 已从识别稿清理 "
                f"{source_labels_removed} 处高置信角色标签。"
            )
        prompt_context, _ = _strip_known_speaker_labels(prev_context)
        prompt = self._build_prompt(prompt_batch_srt, prompt_context, reference_lyrics)
        max_retries = 4
        speaker_cleanup_retry = False
        lyric_alignment_retry = False

        for attempt in range(max_retries):
            if self.cancel_event and self.cancel_event.is_set():
                raise RuntimeError("用户中断：翻译线程已终止")

            try:
                self.api_calls += 1
                request_prompt = prompt
                if speaker_cleanup_retry:
                    request_prompt += (
                        "\n\n【本批次修正】上次输出仍含疑似说话人冠名。"
                        "删除每句开头及句中用于区分角色的‘姓名：’或‘姓名 台词’标签，"
                        "但必须保留自我介绍、称呼别人、英文歌词、歌名、反复句以及句意中自然出现的真实姓名。"
                    )
                if lyric_alignment_retry:
                    request_prompt += (
                        "\n\n【本批次歌词对齐修正】上次输出把后一时间轴的歌词提前写进前一轴，造成相邻字幕重复。"
                        "请按每个时间轴的粗听片段重新拆分日文和中文；前一轴只保留前半句，"
                        "后一轴保留后半句，不得删除时间轴或把后续歌词前移。"
                    )
                response = self.client.chat.completions.create(
                    model=target_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "你是严谨的日语 Live 字幕翻译与排版助手，只返回 SRT。",
                        },
                        {"role": "user", "content": request_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=8192,
                )
                self._record_api_response(response, target_model, batch_idx, attempt)
                choice = response.choices[0]
                if getattr(choice, "finish_reason", "") == "length":
                    raise RuntimeError("API 返回触发长度限制，结果可能残缺。")

                result_text = _clean_model_output(choice.message.content or "")
                result_text, removed_labels = _strip_known_speaker_labels(result_text)
                if removed_labels:
                    with self.failure_lock:
                        self.speaker_labels_removed += removed_labels
                    print(
                        f"      批次 {batch_idx + 1}/{total_batches} 已清理 {removed_labels} 处高置信角色标签。"
                    )
                if not _looks_like_srt(result_text):
                    raise RuntimeError("API 没有返回有效 SRT。")
                if result_text.count("-->") < max(1, len(batch) * 0.6):
                    raise RuntimeError("API 返回字幕数量明显不足。")

                labels = _suspected_speaker_labels(result_text)
                if labels and not speaker_cleanup_retry:
                    speaker_cleanup_retry = True
                    print(
                        f"      批次 {batch_idx + 1}/{total_batches} 检测到疑似说话人冠名，正在局部重试: "
                        + ", ".join(labels)
                    )
                    continue
                if labels:
                    with self.failure_lock:
                        self.speaker_label_warnings += len(labels)
                    print(
                        f"      [提醒] 批次 {batch_idx + 1}/{total_batches} 仍有疑似说话人冠名，请复核: "
                        + ", ".join(labels)
                    )

                lyric_issues = []
                if reference_lyrics and self.output_mode == "bilingual":
                    lyric_issues = _suspected_lyric_sequence_issues(result_text, batch)
                if lyric_issues and not lyric_alignment_retry:
                    lyric_alignment_retry = True
                    print(
                        f"      批次 {batch_idx + 1}/{total_batches} 检测到相邻歌词重复，正在局部重试: "
                        + ", ".join(lyric_issues)
                    )
                    continue
                if lyric_issues:
                    with self.failure_lock:
                        self.lyric_alignment_warnings += len(lyric_issues)
                    print(
                        f"      [提醒] 批次 {batch_idx + 1}/{total_batches} 仍有相邻歌词重复，请复核: "
                        + ", ".join(lyric_issues)
                    )

                restored = self._restore_timestamps(result_text, batch)
                self._save_to_cache(cache_key, restored)
                return batch_idx, restored
            except Exception as exc:
                message = str(exc)
                self._record_api_error(message, target_model, batch_idx, attempt)
                if "用户中断" in message or "401" in message or "403" in message:
                    raise

                if attempt == max_retries - 1:
                    print(f"      批次 {batch_idx + 1}/{total_batches} 多次失败，保留原文不中断全局: {message}")
                    with self.failure_lock:
                        self.failed_batches += 1
                    return batch_idx, original_batch_srt

                sleep_seconds = 8 * (attempt + 1) if "429" in message else 3 * (attempt + 1)
                print(f"      批次 {batch_idx + 1}/{total_batches} 失败，{sleep_seconds}s 后重试: {message}")
                time.sleep(sleep_seconds)

        with self.failure_lock:
            self.failed_batches += 1
        return batch_idx, original_batch_srt

    def _build_prompt_legacy_unused(self, srt_batch_text: str, prev_context: str, reference_lyrics: str) -> str:
        if self.output_mode == "chinese_only":
            output_rules = """
6. 每个时间轴只输出一行中文。
7. 输出中不要包含日文平假名、片假名；人名可音译或保留罗马字。
8. 无意义杂音、纯和声可删除，但不要输出解释。
"""
        else:
            output_rules = """
6. 每个时间轴最多输出两行：第一行校正后的日文，第二行中文。
7. 日文和中文都不要在同一语言内部拆成多行。
8. 无意义杂音、纯和声可删除，但不要输出解释。
"""

        parts = [
            "【任务】根据粗听 SRT 和可选参考歌词，校正日文并翻译字幕。",
            "【硬性规则】",
            "1. 只返回纯 SRT，不要前言、后记、Markdown 或解释。",
            "2. 严格保留 00:00:00,000 --> 00:00:00,000 时间轴格式。",
            "3. 不要合并时间轴；能翻译的块尽量逐块输出。",
            "4. 如果参考歌词没有对应内容，按听写内容进行盲翻。",
            "5. 不要输出“无对应歌词”“这里是和声”等说明文字。",
            self._layout_prompt_rules(),
            output_rules.strip(),
        ]
        if reference_lyrics:
            parts.append(f"【参考歌词】\n{reference_lyrics}")
        if prev_context:
            parts.append(f"【上一小段上下文，仅供理解，不要输出】\n{prev_context}")
        parts.append(f"【当前批次 SRT】\n{srt_batch_text}")
        return "\n\n".join(parts)

    def _layout_prompt_rules_legacy_unused(self) -> str:
        ch_chars = max(10, self.ch_max_units // 2)
        jp_chars = max(14, self.jp_max_units // 2)
        if self.layout_profile in {"square_safe", "portrait_safe"}:
            return (
                "【窄画幅排版】当前视频是正方形或竖屏，字幕空间较窄。"
                f"中文每个时间轴尽量控制在 {ch_chars} 个汉字左右，"
                f"日文尽量控制在 {jp_chars} 个假名/汉字左右。"
                "遇到高速 Live 歌词时优先短句、少连接词、保留关键词；"
                "不要为了变短而丢掉核心意思，也不要在 SRT 内自行换行。"
            )
        if self.layout_profile == "classic_4_3":
            return (
                "【窄屏排版】当前视频不是宽屏，中文翻译保持简洁，避免很长的解释句；"
                "不要在 SRT 内自行换行，后续 ASS 会负责安全换行。"
            )
        return (
            "【Live 排版】保持 v3 的逐轴字幕习惯：一句歌词对应一个时间轴，"
            "中文翻译要像字幕而不是长句解释；不要在 SRT 内自行换行。"
        )

    def _build_prompt(self, srt_batch_text: str, prev_context: str, reference_lyrics: str) -> str:
        if self.output_mode == "chinese_only":
            output_rules = """
6. 每个时间轴只输出一行中文。
7. 输出中不要包含日文平假名、片假名；真实人名可音译或保留罗马字。
8. 字幕不采用“姓名：台词”或“姓名 台词”的说话人冠名格式；真实人名在句意中可正常保留。
9. 无意义杂音、纯和声可删除，但不要输出解释。
"""
        else:
            output_rules = """
6. 每个时间轴最多输出两行：第一行校正后的日文，第二行中文。
7. 日文和中文都不要在同一语言内部拆成多行。
8. 如果有参考歌词，必须优先用参考歌词校正日文原文；不要照抄粗听中的错误假名或片假名。
9. 字幕不采用“姓名：台词”或“姓名 台词”的说话人冠名格式；真实人名在句意中可正常保留。
10. 无意义杂音、纯和声可删除，但不要输出解释。
"""

        parts = [
            "【任务】根据粗听 SRT 和可选参考歌词，校正日文并翻译字幕。",
            "【硬性规则】",
            "1. 只返回纯 SRT，不要前言、后记、Markdown 或解释。",
            "2. 严格保留 00:00:00,000 --> 00:00:00,000 时间轴格式。",
            "3. 严禁合并、拆分、改写时间轴；输入有几个时间段，就按同样顺序输出几个时间段。",
            "4. 有参考歌词时，歌词行优先用于校正演唱段；访谈、MC、串场按听写内容直译。",
            "5. 参考歌词没有对应内容时，必须按粗听内容盲翻，不要跳过。",
            "6. 不要输出“无对应歌词”“这里是和声”“听不清”等说明文字。",
            "7. 不要为了变短而省略后半句；需要压缩时保留主语、动作和情绪。",
            "8. 输入开头疑似说话人名称只用于理解，不要作为字幕冠名前缀输出。",
            "9. 不要删除句意中的姓名：自我介绍、称呼别人、歌词正文里自然出现的人名必须保留。",
            "10. 英文歌词、歌名和反复句属于字幕正文，即使反复出现在句首也必须完整保留。",
            self._proper_noun_prompt_rules(),
            self._layout_prompt_rules(),
            output_rules.strip(),
        ]
        if reference_lyrics:
            parts.append(
                "【歌词顺序对齐】同一行歌词如果被相邻时间轴拆开，必须按各时间轴实际听到的片段分配，"
                "不要在前一轴写完整句、后一轴又重复句尾。除非粗听明确是重复演唱，"
                "不要把参考歌词中的同一句连续分配给多个时间轴；无法可靠对应时保留当前粗听，"
                "不要提前套用下一句歌词。"
            )
            parts.append(f"【参考歌词】\n{reference_lyrics}")
        if prev_context:
            parts.append(f"【上一小段上下文，仅供理解，不要输出】\n{prev_context}")
        parts.append(f"【当前批次 SRT】\n{srt_batch_text}")
        return "\n\n".join(parts)

    def _proper_noun_prompt_rules(self) -> str:
        rules = (
            "【专名保护】不得把不完整、同音或含糊的称呼擅自扩写成另一个姓名；"
            "不确定时保留原文或罗马字，不要猜姓氏。"
        )
        if _proper_noun_profile(self.context_title) == "yama":
            rules += (
                " 当前内容与艺人 yama 有关：日文中的「山さん」必须校正并保留为「yamaさん」，"
                "「山です」按语境校正为「yamaです」；中文译文统一写「yama」，"
                "不得翻译成“山”，也不得扩写成“山田”等其他姓名。"
            )
        return rules

    def _layout_prompt_rules(self) -> str:
        ch_chars = max(10, self.ch_max_units // 2)
        jp_chars = max(14, self.jp_max_units // 2)
        if self.layout_profile in {"square_safe", "portrait_safe"}:
            return (
                "【窄画幅排版】当前视频是正方形或竖屏，字幕空间较窄。"
                f"中文每个时间轴尽量控制在 {ch_chars} 个汉字左右，"
                f"日文尽量控制在 {jp_chars} 个假名/汉字左右。"
                "遇到高语速 Live 歌词时优先短句、少连接词、保留关键词；"
                "不要为了变短而丢掉核心意思，也不要在 SRT 内自行换行。"
            )
        if self.layout_profile == "classic_4_3":
            return (
                "【窄屏排版】当前视频不是宽屏，中文翻译保持简洁，避免很长的解释句；"
                "不要在 SRT 内自行换行，后续 ASS 会负责安全换行。"
            )
        return (
            "【Live 排版】保持 v3 的逐轴字幕习惯：一句歌词对应一个时间轴，"
            "中文翻译要像字幕而不是长句解释；不要在 SRT 内自行换行。"
        )

    def _restore_timestamps(self, translated_content: str, original_blocks: list[SrtBlock]) -> str:
        translated_blocks = _parse_srt_text(translated_content)
        restored: list[str] = []
        by_time: dict[str, list[SrtBlock]] = {}
        by_index: dict[int, list[SrtBlock]] = {}
        for block in translated_blocks:
            by_time.setdefault(_normalize_time_line(block.time_line), []).append(block)
            if block.index_explicit:
                by_index.setdefault(block.index, []).append(block)

        used: set[int] = set()
        fallback_count = 0
        for original in original_blocks:
            translated = _take_unused(by_time.get(_normalize_time_line(original.time_line), []), used)
            if translated is None:
                translated = _take_unused(by_index.get(original.index, []), used)
            if translated is None:
                fallback_count += 1
            text_lines = translated.text_lines if translated and translated.text_lines else original.text_lines
            filtered = self._filter_text_lines(text_lines)
            if not filtered:
                continue
            restored.append(f"{original.index}\n{original.time_line}\n" + "\n".join(filtered))

        if fallback_count:
            with self.failure_lock:
                self.alignment_fallbacks += fallback_count
            print(f"      [时间轴保护] {fallback_count} 条译文未可靠对齐，已保留对应原文，未顺移后续字幕。")

        return "\n\n".join(restored)

    def _reindex_srt(self, srt_content: str) -> str:
        blocks = _parse_srt_text(srt_content)
        reindexed: list[str] = []

        for idx, block in enumerate(blocks, start=1):
            text_lines = self._filter_text_lines(block.text_lines)
            if not text_lines:
                continue
            reindexed.append(f"{idx}\n{block.time_line}\n" + "\n".join(text_lines))

        return "\n\n".join(reindexed)

    def _filter_text_lines(self, text_lines: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(line.strip() for line in text_lines if line.strip())
        if self.output_mode != "chinese_only":
            return cleaned[:2] if len(cleaned) > 2 else cleaned

        if len(cleaned) > 1:
            return (cleaned[-1],)

        chinese_or_plain = tuple(line for line in cleaned if not _contains_kana(line))
        return (chinese_or_plain or cleaned)[:1]

    def _load_cache(self) -> dict[str, str]:
        if not self.cache_file.exists():
            return {}
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                print(f"   -> 已加载翻译缓存 {len(data)} 个批次。")
                return {str(key): str(value) for key, value in data.items()}
        except Exception as exc:
            print(f"   -> 翻译缓存读取失败: {exc}")
        return {}

    def _save_to_cache(self, cache_key: str, text: str) -> None:
        with self.cache_lock:
            self.translation_cache[cache_key] = text
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(
                    json.dumps(self.translation_cache, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

    def _start_api_trace(self, active_model: str, reference_lyrics_present: bool) -> None:
        started_at = _utc_now()
        with self.api_trace_lock:
            self.api_trace = {
                "schema_version": 2,
                "status": "running",
                "started_at": started_at,
                "finished_at": None,
                "prompt_policy": PROMPT_POLICY_VERSION,
                "model_policy": "user_selected_v1",
                "api_base_url": self.base_url,
                "request_timeout_seconds": API_REQUEST_TIMEOUT_SECONDS,
                "configured_model": self.primary_model,
                "requested_model": active_model,
                "reference_lyrics_present": reference_lyrics_present,
                "output_mode": self.output_mode,
                "layout_profile": self.layout_profile,
                "ch_max_units": self.ch_max_units,
                "jp_max_units": self.jp_max_units,
                "proper_noun_profile": _proper_noun_profile(self.context_title),
                "calls": [],
            }
            self.api_trace_announced = False
            self._write_api_trace_locked()

    def _record_api_response(self, response, requested_model: str, batch_idx: int, attempt: int) -> None:
        usage = getattr(response, "usage", None)
        entry = {
            "event": "response",
            "received_at": _utc_now(),
            "batch": batch_idx + 1,
            "attempt": attempt + 1,
            "requested_model": requested_model,
            "returned_model": str(getattr(response, "model", "") or ""),
            "response_id": str(getattr(response, "id", "") or ""),
            "system_fingerprint": str(getattr(response, "system_fingerprint", "") or ""),
            "created": getattr(response, "created", None),
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            },
        }
        with self.api_trace_lock:
            returned_model = str(entry["returned_model"] or "").strip()
            if returned_model:
                self.returned_models.add(returned_model)
            calls = self.api_trace.setdefault("calls", [])
            if isinstance(calls, list):
                calls.append(entry)
            self._write_api_trace_locked()
            if not self.api_trace_announced:
                returned_model = entry["returned_model"] or requested_model
                print(f"   -> 翻译服务已确认: {returned_model}")
                print(f"   -> 调用记录已保存: {self.api_trace_file}")
                self.api_trace_announced = True

    def _record_cache_hit(self, requested_model: str, batch_idx: int) -> None:
        entry = {
            "event": "cache_hit",
            "received_at": _utc_now(),
            "batch": batch_idx + 1,
            "requested_model": requested_model,
        }
        with self.api_trace_lock:
            calls = self.api_trace.setdefault("calls", [])
            if isinstance(calls, list):
                calls.append(entry)
            self._write_api_trace_locked()
            if not self.api_trace_announced:
                print(f"   -> 本次复用 {requested_model} 的翻译缓存。")
                print(f"   -> 调用记录已保存: {self.api_trace_file}")
                self.api_trace_announced = True

    def _record_api_error(self, message: str, requested_model: str, batch_idx: int, attempt: int) -> None:
        safe_message = str(message or "unknown error")
        if self.api_key:
            safe_message = safe_message.replace(self.api_key, "[REDACTED]")
        entry = {
            "event": "error",
            "received_at": _utc_now(),
            "batch": batch_idx + 1,
            "attempt": attempt + 1,
            "requested_model": requested_model,
            "message": safe_message[:1000],
        }
        with self.api_trace_lock:
            calls = self.api_trace.setdefault("calls", [])
            if isinstance(calls, list):
                calls.append(entry)
            self._write_api_trace_locked()

    def _finish_api_trace(self, status: str, error: str = "") -> None:
        with self.api_trace_lock:
            self.api_trace["status"] = status
            self.api_trace["finished_at"] = _utc_now()
            self.api_trace["cache_hits"] = self.cache_hits
            self.api_trace["api_calls"] = self.api_calls
            self.api_trace["failed_batches"] = self.failed_batches
            self.api_trace["speaker_labels_removed"] = self.speaker_labels_removed
            self.api_trace["lyric_alignment_warnings"] = self.lyric_alignment_warnings
            self.api_trace["returned_models"] = sorted(self.returned_models)
            if error:
                safe_error = error.replace(self.api_key, "[REDACTED]") if self.api_key else error
                self.api_trace["final_error"] = safe_error[:1000]
            self._write_api_trace_locked()

    def _write_api_trace_locked(self) -> None:
        try:
            self.api_trace_file.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.api_trace_file.with_suffix(self.api_trace_file.suffix + ".tmp")
            temp_path.write_text(json.dumps(self.api_trace, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.api_trace_file)
        except Exception:
            pass


def _build_http_client(proxy_url: str):
    proxy = _normalize_proxy_url(proxy_url)
    if not proxy:
        print("   -> [网络管家] 翻译请求使用直连或系统环境代理。")
        return None

    try:
        import httpx

        signature = inspect.signature(httpx.Client)
        if "proxy" in signature.parameters:
            client = httpx.Client(proxy=proxy)
        else:
            client = httpx.Client(proxies=proxy)
        print(f"   -> [网络管家] 翻译请求已挂载代理: {proxy}")
        return client
    except ImportError:
        print("   -> 未安装 httpx，无法挂载翻译代理。")
    except Exception as exc:
        print(f"   -> 翻译代理初始化失败，已切回直连: {exc}")
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_proxy_url(proxy_url: str) -> str:
    proxy = str(proxy_url or "").strip().rstrip("/")
    if not proxy:
        return ""
    if re.fullmatch(r"\d{1,5}", proxy):
        port = int(proxy)
        if not 1 <= port <= 65535:
            raise ValueError("代理端口必须在 1-65535 之间。")
        return f"http://127.0.0.1:{port}"
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", proxy):
        return proxy
    return f"http://{proxy}"


def _parse_srt_text(content: str) -> list[SrtBlock]:
    raw_blocks = [block.strip() for block in re.split(r"\n\s*\n", content.strip()) if block.strip()]
    parsed: list[SrtBlock] = []
    for fallback_index, raw_block in enumerate(raw_blocks, start=1):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        time_idx = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if time_idx < 0:
            continue
        digits = re.sub(r"\D", "", lines[0]) if time_idx > 0 else ""
        index = int(digits) if digits else fallback_index
        text_lines = tuple(lines[time_idx + 1 :])
        if text_lines:
            parsed.append(
                SrtBlock(
                    index=index,
                    time_line=lines[time_idx],
                    text_lines=text_lines,
                    index_explicit=bool(digits),
                )
            )
    return parsed


def _normalize_time_line(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _take_unused(candidates: list[SrtBlock], used: set[int]) -> SrtBlock | None:
    for candidate in candidates:
        identity = id(candidate)
        if identity not in used:
            used.add(identity)
            return candidate
    return None


def _suspected_speaker_labels(content: str) -> list[str]:
    labels: set[str] = set()
    for line in content.splitlines():
        text = line.strip()
        if not text or text.isdigit() or "-->" in text:
            continue
        colon = SPEAKER_COLON_RE.match(text)
        if colon:
            labels.add(colon.group("label").strip())
            continue
        bare = SPEAKER_BARE_RE.match(text)
        if bare and bare.group("label").strip().casefold() in KNOWN_SPEAKER_LABELS:
            labels.add(bare.group("label").strip())
    return sorted(labels, key=str.casefold)


def _suspected_lyric_sequence_issues(content: str, source_blocks: list[SrtBlock]) -> list[str]:
    translated = _parse_srt_text(content)
    by_time = {_normalize_time_line(block.time_line): block for block in translated}
    issues: list[str] = []
    for left_source, right_source in zip(source_blocks, source_blocks[1:]):
        left = by_time.get(_normalize_time_line(left_source.time_line))
        right = by_time.get(_normalize_time_line(right_source.time_line))
        if left is None or right is None or not left.text_lines or not right.text_lines:
            continue

        left_text = _normalize_lyric_text(left.text_lines[0])
        right_text = _normalize_lyric_text(right.text_lines[0])
        source_left = _normalize_lyric_text(left_source.text_lines[0])
        source_right = _normalize_lyric_text(right_source.text_lines[0])
        if not _is_repeated_lyric_tail(left_text, right_text):
            continue
        if _is_repeated_lyric_tail(source_left, source_right):
            continue
        issues.append(f"{left_source.index}->{right_source.index}")
    return issues


def _normalize_lyric_text(text: str) -> str:
    return re.sub(r"[\s、。！？,.!?;；:：'\"“”‘’（）()\[\]【】]", "", str(text or "")).casefold()


def _is_repeated_lyric_tail(left: str, right: str) -> bool:
    return len(right) >= 4 and (left == right or (len(left) > len(right) and left.endswith(right)))


def _strip_known_speaker_labels(content: str) -> tuple[str, int]:
    removed = 0
    cleaned_lines: list[str] = []
    for line in str(content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            cleaned_lines.append(line)
            continue

        def replace_label(match: re.Match[str]) -> str:
            nonlocal removed
            following = line[match.end() :].lstrip()
            if SPEAKER_HONORIFIC_RE.match(following):
                return match.group(0)
            removed += 1
            return match.group("prefix")

        cleaned = KNOWN_SPEAKER_TOKEN_RE.sub(replace_label, line)
        cleaned_lines.append(re.sub(r"[ \t]{2,}", " ", cleaned).strip())
    return "\n".join(cleaned_lines), removed


def _blocks_to_srt(blocks: list[SrtBlock]) -> str:
    return "\n\n".join(block.to_srt() for block in blocks)


def _same_subtitle_payload(left: str, right: str) -> bool:
    return _subtitle_payload(left) == _subtitle_payload(right)


def _subtitle_payload(content: str) -> list[str]:
    payload: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        payload.append(stripped)
    return payload


def _batch_cache_key(
    blocks: list[SrtBlock],
    output_mode: str,
    target_model: str,
    layout_profile: str,
    ch_max_units: int,
    jp_max_units: int,
    reference_lyrics: str,
    context_title: str,
) -> str:
    payload = {
        "prompt_policy": PROMPT_POLICY_VERSION,
        "mode": output_mode,
        "model": target_model,
        "layout_profile": layout_profile,
        "ch_max_units": ch_max_units,
        "jp_max_units": jp_max_units,
        "proper_noun_profile": _proper_noun_profile(context_title),
        "reference_lyrics_md5": hashlib.md5(reference_lyrics.encode("utf-8")).hexdigest()
        if reference_lyrics
        else "",
        "blocks": [block.to_srt() for block in blocks],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _proper_noun_profile(context_title: str) -> str:
    return "yama" if re.search(r"(?i)(?<![A-Za-z])yama(?![A-Za-z])", str(context_title or "")) else ""


def _clean_model_output(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    cleaned = cleaned.replace("```srt", "").replace("```SRT", "").replace("```", "").strip()
    return cleaned


def _looks_like_srt(text: str) -> bool:
    return "-->" in text and bool(re.search(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->", text))


def _contains_kana(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff]", text))


def _normalize_output_mode(output_mode: str) -> str:
    raw = str(output_mode or "").strip().lower()
    if raw == "chinese_only" or "纯中文" in raw or "chinese only" in raw:
        return "chinese_only"
    return "bilingual"
