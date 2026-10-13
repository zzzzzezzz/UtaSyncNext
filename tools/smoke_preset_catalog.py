"""Verify the built-in ASS preset catalog renders with the fonts it names.

libass silently substitutes a fallback face when a ``Fontname`` is unknown, so a
preset can look applied while every line actually renders in the wrong
typeface. This smoke renders each preset once and probes each referenced family
against a deliberately bogus name: identical pixels mean the family never
resolved on this machine.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.ass_maker import ASS_PRESETS, BUILTIN_PRESETS
from ui.ass_preview_frame import render_ass_preview_frame

BOGUS_FONT = "UtaSync Missing Font Probe"
REFERENCE_CH = "Microsoft YaHei"
REFERENCE_JP = "Meiryo"
SAMPLE_JP = "この光り輝くステージの上で"
SAMPLE_ZH = "在这光芒万丈的舞台上"
MIN_BRIGHT_PIXELS = 500
REQUIRED_KEYS = (
    "ch_font",
    "jp_font",
    "ch_color",
    "jp_color",
    "outline_size",
    "shadow_size",
)

_digest_cache: dict[tuple[str, str], str] = {}


def _frame_digest(ch_font: str, jp_font: str) -> str:
    key = (ch_font, jp_font)
    cached = _digest_cache.get(key)
    if cached is not None:
        return cached
    config: dict[str, Any] = {"ch_font": ch_font, "jp_font": jp_font, "fade_mode": "硬切"}
    frame = render_ass_preview_frame(config, SAMPLE_JP, SAMPLE_ZH)
    digest = hashlib.sha256(frame.tobytes()).hexdigest()
    _digest_cache[key] = digest
    return digest


def ch_font_resolves(family: str) -> bool:
    return _frame_digest(family, REFERENCE_JP) != _frame_digest(BOGUS_FONT, REFERENCE_JP)


def jp_font_resolves(family: str) -> bool:
    return _frame_digest(REFERENCE_CH, family) != _frame_digest(REFERENCE_CH, BOGUS_FONT)


def _bright_pixel_count(config: dict[str, Any]) -> int:
    frame = render_ass_preview_frame(config, SAMPLE_JP, SAMPLE_ZH)
    histogram = frame.convert("L").histogram()
    return sum(histogram[60:])


def _probe(families: list[str]) -> int:
    for family in families:
        ch_state = "ok" if ch_font_resolves(family) else "MISSING"
        jp_state = "ok" if jp_font_resolves(family) else "MISSING"
        print(f"{family} | ch={ch_state} jp={jp_state}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        nargs="*",
        default=[],
        help="probe these families instead of checking the whole catalog",
    )
    args = parser.parse_args()
    if args.probe:
        return _probe(list(args.probe))

    problems: list[str] = []
    for name, config in ASS_PRESETS.items():
        missing_keys = [key for key in REQUIRED_KEYS if key not in config]
        if missing_keys:
            problems.append(f"{name}: 缺少配置项 {', '.join(missing_keys)}")
            continue
        ch_font = str(config["ch_font"])
        jp_font = str(config["jp_font"])
        if ch_font.strip().casefold() == jp_font.strip().casefold():
            problems.append(f"{name}: 中日字体相同，双语排版没有差异 -> {ch_font}")
        ch_ok = ch_font_resolves(ch_font)
        jp_ok = jp_font_resolves(jp_font)
        bright = _bright_pixel_count(config)
        if bright < MIN_BRIGHT_PIXELS:
            problems.append(f"{name}: 渲染结果没有可见字幕（亮色像素 {bright}）")
        if not ch_ok:
            problems.append(f"{name}: 中文字体在本机无法解析 -> {ch_font}")
        if not jp_ok:
            problems.append(f"{name}: 日文字体在本机无法解析 -> {jp_font}")
        state = "OK" if ch_ok and jp_ok else "FONT"
        print(f"{state:4} {name} | 中文 {ch_font} | 日文 {jp_font} | 亮色像素 {bright}")

    if len(ASS_PRESETS) != len(BUILTIN_PRESETS):
        problems.append("ASS_PRESETS 与 BUILTIN_PRESETS 数量不一致")
    if len(BUILTIN_PRESETS) < 9:
        problems.append(f"内置预设数量不足 9 套（当前 {len(BUILTIN_PRESETS)}）")
    for name, config in BUILTIN_PRESETS.items():
        if not str(config.get("description", "")).strip():
            problems.append(f"{name}: 缺少 description，侧栏卡片会退化成占位文案")

    print(f"\n共 {len(ASS_PRESETS)} 套预设")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print("PASS: 所有预设都能渲染，且引用的字体在本机可解析")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
