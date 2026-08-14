from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_LEADING_MARKS_RE = re.compile(r"^[\s>:\-\=\[\]\d/\\|._、，。:：;；!！?？A-Za-z]*[\U0001F000-\U0001FAFF\u2600-\u27BF]+[\ufe0f\s]*")
_SCHEDULE_PREFIX_RE = re.compile(r"^\s*->\s*")


def format_console_line(line: str) -> str:
    """Convert worker/debug logs into stable user-facing Chinese."""
    line_end = "\n" if line.endswith("\n") else ""
    raw = line.rstrip("\n")
    stripped = raw.strip()

    if not stripped:
        return line
    if set(stripped) == {"="}:
        return line_end

    formatted = _format_known_line(stripped)
    if formatted is not None:
        return formatted + line_end

    cleaned = _clean_decorations(stripped)
    cleaned = _format_soft_prefix(cleaned)
    return cleaned + line_end


def format_status_text(text: str) -> str:
    """Shorten progress/status text for the sidebar run button."""
    formatted = format_console_line(text).strip()
    if not formatted:
        return text
    if formatted.endswith("。"):
        formatted = formatted[:-1]
    return formatted


def format_run_summary(summary: dict[str, Any]) -> str:
    """Build a compact post-run summary for normal users."""
    lines = ["\n任务：本次处理总览"]

    asr_cache_hit = bool(summary.get("asr_cache_hit"))
    asr_backend = str(summary.get("asr_backend") or "")
    asr_elapsed = _format_duration(summary.get("asr_elapsed_seconds"))
    if asr_cache_hit or asr_backend == "cache":
        lines.append("打轴：复用已有字幕结果，未重新识别。")
    else:
        backend_label = _backend_label(asr_backend)
        elapsed_text = f"，耗时 {asr_elapsed}" if asr_elapsed else ""
        fallback_text = "，期间曾退回 CPU" if summary.get("cuda_fallback_used") else ""
        lines.append(f"打轴：{backend_label}{elapsed_text}{fallback_text}。")

    density_splits = int(summary.get("subtitle_density_splits") or 0)
    density_extra_cues = int(summary.get("subtitle_density_extra_cues") or 0)
    if density_splits:
        lines.append(
            f"字幕长度：已自动整理 {density_splits} 条过长字幕，拆成连续短轴"
            f"（增加 {density_extra_cues} 轴），文字和原时间范围均保留。"
        )

    if summary.get("audio_cache_hit"):
        lines.append("音频：复用缓存，跳过提取和分离。")
    elif summary.get("demucs_skipped"):
        lines.append("人声分离：已跳过，直接识别原音频。")
    else:
        lines.append("人声分离：已启用。")

    if summary.get("translation_requested"):
        translation_elapsed = _format_duration(summary.get("translation_elapsed_seconds"))
        batches = int(summary.get("translation_batches") or 0)
        cache_hits = int(summary.get("translation_cache_hits") or 0)
        api_calls = int(summary.get("translation_api_calls") or 0)
        failed = int(summary.get("translation_failed_batches") or 0)
        parts = ["调用翻译接口"]
        if translation_elapsed:
            parts.append(f"耗时 {translation_elapsed}")
        if batches:
            parts.append(f"共 {batches} 批")
        if cache_hits:
            parts.append(f"缓存命中 {cache_hits} 批")
        if api_calls:
            parts.append(f"接口请求 {api_calls} 次")
        if failed:
            parts.append(f"失败 {failed} 批")
        lines.append("翻译：" + "，".join(parts) + "。")
        alignment_fallbacks = int(summary.get("translation_alignment_fallbacks") or 0)
        speaker_warnings = int(summary.get("translation_speaker_label_warnings") or 0)
        if alignment_fallbacks:
            lines.append(
                f"翻译保护：有 {alignment_fallbacks} 条内容未能可靠对应，已保留该处原文，后续字幕没有顺移。"
            )
        if speaker_warnings:
            lines.append(f"复核提醒：仍检测到 {speaker_warnings} 类疑似说话人冠名，请检查对应字幕。")
    else:
        lines.append("翻译：未配置接口，本次只生成原文字幕。")

    output_mode = "纯中文字幕" if summary.get("output_mode") == "chinese_only" else "中日双语字幕"
    ass_text = "已生成 ASS 特效字幕" if summary.get("ass_generated") else "未生成 ASS"
    lines.append(f"字幕：输出模式为{output_mode}，{ass_text}。")

    total_elapsed = _format_duration(summary.get("total_elapsed_seconds"))
    if total_elapsed:
        lines.append(f"总耗时：{total_elapsed}。")

    return "\n".join(lines) + "\n"


def _format_known_line(text: str) -> str | None:
    old_friendly = _format_previous_friendly_line(text)
    if old_friendly is not None:
        return old_friendly

    if "拖拽引擎已激活" in text:
        return "系统：拖拽导入已启用，可直接拖入媒体或歌词文件。"
    if "未检测到 tkinterdnd2" in text:
        return "提示：拖拽组件未安装，暂时不能拖入文件。"
    if "拖拽注册失败" in text:
        return "提示：拖拽导入启动失败，请用按钮选择文件。"

    if "[拖拽加载成功]" in text:
        return "歌词：已导入参考歌词。" + _suffix_after_colon(text)
    if "[媒体挂载成功]" in text:
        return "媒体：已选择输入文件：" + _suffix_after_colon(text)

    if "[任务预检]" in text:
        return "任务：开始前检查"
    if text.startswith("已启动 Next 独立管线 worker"):
        return "系统：已启动独立处理进程。"
    if text.startswith("已请求取消"):
        return "任务：已请求取消，正在等待处理进程退出。"
    if "worker 未及时退出" in text:
        return "任务：处理进程未及时退出，正在强制结束。"
    if "worker 已退出" in text:
        return "任务：处理进程已退出。"
    if text.startswith("错误日志已保存"):
        return "日志：错误详情已保存：" + _short_file_name(_suffix_after_colon(text))
    if text.startswith("错误日志保存失败"):
        return "日志：错误详情保存失败：" + _suffix_after_colon(text)
    if "未选择媒体，仅运行就绪检查" in text:
        return "系统：未选择媒体，正在检查独立进程是否可用。"
    if text.startswith("就绪检查进行中"):
        pct = _last_percent(text)
        return f"系统：就绪检查 {pct}" if pct else "系统：正在检查就绪状态。"
    if text.startswith("就绪检查完成"):
        return "系统：就绪检查完成，独立进程可用。"

    if "[Next 任务总线启动]" in text:
        return "任务：开始处理：" + _suffix_after_colon(text)
    if "[Next 调度]" in text:
        return "任务：本次处理参数"
    if "[模型预检]" in text:
        model_match = re.search(r"模型:\s*(.+)$", text)
        if model_match:
            return "模型：使用本地打轴模型：" + _strip_snapshot_hash(model_match.group(1).strip())
        return "模型：正在检查本地打轴模型。"

    if "检测到有效音频缓存" in text:
        return "缓存：已找到可复用音频，跳过提取和分离。"
    if "打轴缓存:" in text or "打轴缓存：" in text:
        if "未命中" in text:
            return "缓存：没有可复用打轴结果，本次会重新识别。"
        if "命中" in text:
            return "缓存：已有可复用打轴结果，本次会跳过重新识别。"
        return "缓存：没有可复用打轴结果，本次会重新识别。"
    if "翻译缓存:" in text or "翻译缓存：" in text:
        if "未命中" in text:
            return "缓存：没有可复用翻译结果，本次会请求接口。"
        if "命中" in text:
            return "缓存：已有可复用翻译结果，本次不会重新请求接口。"
        return "缓存：没有可复用翻译结果，本次会请求接口。"
    if "[断点续传]" in text:
        return "缓存：已从上次结果继续处理。"

    asr = _format_asr_line(text)
    if asr is not None:
        return asr

    if "[性能报告]" in text:
        return "性能：打轴阶段耗时：" + _suffix_after_colon(text)
    if "[网络管家]" in text:
        if "代理" in text and "挂载" in text:
            return "网络：翻译请求正在使用代理：" + _suffix_after_colon(text)
        return "网络：翻译请求使用直连或系统代理。"

    if "[Next 翻译阶段]" in text:
        return "翻译：启动大模型翻译。"
    if text.startswith("AI 语境翻译中"):
        pct = _last_percent(text)
        return f"翻译：进度 {pct}" if pct else "翻译：正在处理。"
    if text == "翻译排版完成":
        return "翻译：排版完成。"
    if "未检测到参考歌词" in text:
        return "翻译：未提供参考歌词，本次使用盲翻。"
    if "启动翻译队列" in text:
        match = re.search(r"并发\s*(\d+).*?共\s*(\d+)\s*个批次", text)
        if match:
            return f"翻译：开始处理，共 {match.group(2)} 批，并发 {match.group(1)}。"
        return "翻译：开始处理字幕。"
    if "[翻译进度]" in text:
        pct = _last_percent(text)
        return f"翻译：进度 {pct}" if pct else "翻译：进度更新。"
    if text.startswith("[完成]") and "翻译完成" in text:
        seconds = re.search(r"耗时\s*(\d+)\s*秒", text)
        return f"翻译：完成，用时 {seconds.group(1)} 秒。" if seconds else "翻译：完成。"
    if text.startswith("[输出]") and "翻译字幕已保存" in text:
        return "字幕：翻译字幕已保存：" + _short_file_name(_suffix_after_colon(text))

    if "[ASS 引擎]" in text:
        return "字幕：正在生成特效字幕。"
    if "正在生成 ASS 特效字幕" in text:
        return "字幕：正在生成特效字幕。"
    if "已应用自定义字幕样式" in text:
        return "字幕：已应用自定义样式。"
    if "特效花字已存盘" in text:
        return "字幕：ASS 特效字幕已保存：" + _suffix_after_colon(text)
    if "[输出结果]" in text:
        return "输出：已更新输出目录：" + _suffix_after_colon(text)
    if "Next 管线处理完成" in text:
        return "任务：处理完成。"

    if "[路径已复制]" in text:
        return "输出：路径已复制：" + _suffix_after_bracket(text)
    if "[缓存清理]" in text:
        return "缓存：" + _suffix_after_bracket(text)
    if "[压制启动]" in text:
        return "压制：" + text.split("]", 1)[-1].strip()
    if "[硬字幕成品]" in text:
        return "压制：硬字幕视频已生成：" + _suffix_after_bracket(text)
    if "[压制失败]" in text:
        return "压制：失败：" + _suffix_after_bracket(text)

    return None


def _format_asr_line(text: str) -> str | None:
    if text == "Loading ASR model...":
        return "打轴：正在加载识别模型。"
    if text.startswith("ASR transcribing"):
        pct = _last_percent(text)
        return f"打轴：进度 {pct}" if pct else "打轴：正在识别音频。"
    if text.startswith("[ASR] Loading Faster-Whisper model:"):
        return "打轴：正在加载识别模型。"
    if text == "[ASR] CPU mode requested by UTASYNCNEXT_FORCE_CPU_ASR=1.":
        return "打轴：已按设置强制使用 CPU 模式。"
    if text == "[ASR] CUDA runtime DLLs are not complete; using CPU int8.":
        return "打轴：未检测到完整显卡运行库，已改用 CPU 模式，速度会明显变慢。"
    if text == "[ASR] CPU int8 backend selected.":
        return "打轴：正在使用 CPU 模式。"
    if text.startswith("[ASR] CUDA ") and text.endswith(" backend selected."):
        mode = text.replace("[ASR] CUDA ", "").replace(" backend selected.", "")
        return f"打轴：正在使用显卡加速（CUDA {mode}）。"
    if text.startswith("[ASR] CUDA ") and " model load failed:" in text:
        mode = text.split(" model load failed:", 1)[0].replace("[ASR] CUDA ", "")
        return f"打轴：显卡模式 {mode} 加载失败，正在尝试更省显存的模式。"
    if text.startswith("[ASR] CUDA runtime failed during transcription:"):
        return "打轴：显卡运行中断，准备自动切换模式重试。"
    if text == "[ASR] Falling back to CPU int8 and retrying once...":
        return "打轴：已切到 CPU 模式并重试一次。"
    if text == "[ASR] Falling back to CPU int8...":
        return "打轴：显卡不可用，已改用 CPU 模式。"
    if text.startswith("[ASR] Start transcription"):
        return "打轴：开始识别音频。"
    if text.startswith("[ASR] progress "):
        pct = _last_percent(text)
        return f"打轴：进度 {pct}" if pct else "打轴：进度更新。"
    if text.startswith("[ASR] SRT saved:"):
        return "字幕：SRT 已保存：" + _short_file_name(text.split(":", 1)[1].strip())
    if text == "[ASR] No valid subtitle segments were emitted.":
        return "提示：识别完成，但没有生成有效字幕段。"
    return None


def _format_previous_friendly_line(text: str) -> str | None:
    cleaned = _clean_decorations(text)
    if cleaned.startswith("提取源音频"):
        return "音频：正在提取源音频。"
    if cleaned.startswith("跳过人声分离"):
        return "人声分离：本次跳过，直接识别原音频。"
    if cleaned.startswith("分离人声中"):
        pct = _last_percent(cleaned)
        return f"人声分离：进度 {pct}" if pct else "人声分离：正在提取人声。"
    if cleaned.startswith("正在加载打轴模型"):
        return "打轴：正在加载识别模型。"
    if cleaned.startswith("未检测到可用 CUDA"):
        return "打轴：未检测到完整显卡运行库，已改用 CPU 模式，速度会明显变慢。"
    if cleaned.startswith("当前打轴后端：GPU CUDA"):
        mode = cleaned.replace("当前打轴后端：GPU CUDA", "").strip(" 。")
        return f"打轴：正在使用显卡加速（CUDA {mode}）。"
    if cleaned.startswith("当前打轴后端：CPU"):
        return "打轴：正在使用 CPU 模式。"
    if cleaned.startswith("GPU") and "加载失败" in cleaned:
        return "打轴：显卡模式加载失败，正在尝试更省显存的模式。"
    if cleaned.startswith("GPU 不可用"):
        return "打轴：显卡不可用，已改用 CPU 模式。"
    if cleaned.startswith("开始听写打轴"):
        return "打轴：开始识别音频。"
    if cleaned.startswith("打轴进度"):
        pct = _last_percent(cleaned)
        return f"打轴：进度 {pct}" if pct else "打轴：进度更新。"
    if cleaned.startswith("SRT 字幕已保存"):
        return "字幕：SRT 已保存：" + _short_file_name(_suffix_after_colon(cleaned))
    if cleaned.startswith("未配置 API Key"):
        return "翻译：未配置接口密钥，本次只生成原文字幕。"
    return None


def _format_soft_prefix(text: str) -> str:
    text = _SCHEDULE_PREFIX_RE.sub("", text)
    if text.startswith("算力策略:") or text.startswith("算力策略："):
        value = _suffix_after_colon(text)
        if "60 秒" in value:
            return "性能：为避免内存或显存过高，按 60 秒分段处理。"
        if "180 秒" in value:
            return "性能：按 180 秒分段，兼顾速度和稳定。"
        if "1800 秒" in value:
            return "性能：使用长分段模式，速度更快但更吃内存和显存。"
        return "性能：策略 " + value
    if text.startswith("分离策略:") or text.startswith("分离策略："):
        value = _suffix_after_colon(text)
        if "跳过" in value:
            return "人声分离：本次跳过，直接识别原音频。"
        return "人声分离：本次会先提取人声。"
    if text.startswith("输出模式:") or text.startswith("输出模式："):
        return "字幕：输出模式 " + _suffix_after_colon(text)
    replacements = (
        ("媒体:", "媒体："),
        ("媒体：", "媒体："),
        ("Whisper:", "打轴：模型 "),
        ("Whisper 模型:", "打轴：模型 "),
        ("Whisper 模型：", "打轴：模型 "),
        ("Demucs:", "人声分离："),
        ("ASS:", "字幕："),
        ("翻译:", "翻译："),
        ("模型:", "翻译：模型 "),
        ("声学模型:", "音频：模式 "),
        ("声学模型：", "音频：模式 "),
    )
    for source, target in replacements:
        if text.startswith(source):
            value = text[len(source):].strip()
            if source.startswith("Demucs") and "跳过" in value:
                return "人声分离：本次跳过，优先快速打轴。"
            if source.startswith("翻译") and "调用 API" in value:
                return "翻译：本次会调用翻译接口。"
            return target + value
    if text.startswith("未配置 API Key"):
        return "翻译：未配置接口密钥，本次只生成原文字幕。"
    if "跳过 Demucs" in text:
        return text.replace("跳过 Demucs", "跳过人声分离").replace("Demucs", "人声分离")
    return text


def _clean_decorations(text: str) -> str:
    text = _LEADING_MARKS_RE.sub("", text).strip()
    text = text.replace("➔", "->")
    text = text.replace("： ", "：")
    return text


def _suffix_after_colon(text: str) -> str:
    if "：" in text:
        return text.split("：", 1)[-1].strip()
    if ":" in text:
        return text.split(":", 1)[-1].strip()
    return _suffix_after_bracket(text)


def _suffix_after_bracket(text: str) -> str:
    if "]" in text:
        return text.split("]", 1)[-1].strip()
    return _clean_decorations(text)


def _last_percent(text: str) -> str:
    matches = re.findall(r"\d+%", text)
    return matches[-1] if matches else ""


def _strip_snapshot_hash(text: str) -> str:
    return re.sub(r"\s*\([0-9a-f]{20,}\)", "", text).strip()


def _short_file_name(value: str) -> str:
    value = value.strip()
    if "\\" not in value and "/" not in value:
        return value
    try:
        return Path(value).name or value
    except Exception:
        return value


def _backend_label(backend: str) -> str:
    if backend.startswith("cuda:"):
        return "使用显卡加速（CUDA " + backend.split(":", 1)[1] + "）"
    if backend.startswith("cpu"):
        return "使用 CPU 模式"
    if backend == "failed":
        return "识别模型加载失败"
    return "已完成识别"


def _format_duration(value: object) -> str:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分 {sec} 秒"
    if minutes:
        return f"{minutes} 分 {sec} 秒"
    return f"{sec} 秒"
