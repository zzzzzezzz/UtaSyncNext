from __future__ import annotations

import argparse
import re
from pathlib import Path


TIMESTAMP_RE = re.compile(
    r"^(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})\s*$"
)
ASS_EVENT_RE = re.compile(
    r"^(?P<prefix>(?:Dialogue|Comment):\s*[^,]*,)"
    r"(?P<start>\d+:\d{2}:\d{2}\.\d{2}),"
    r"(?P<end>\d+:\d{2}:\d{2}\.\d{2})(?P<suffix>,.*)$"
)


def _to_milliseconds(value: str) -> int:
    hours, minutes, seconds_and_ms = value.split(":")
    seconds, milliseconds = seconds_and_ms.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(milliseconds)
    )


def _from_milliseconds(value: int) -> str:
    if value < 0:
        raise ValueError("偏移后的时间不能小于 00:00:00,000")
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def shift_srt(content: str, offset_ms: int) -> str:
    output: list[str] = []
    shifted = 0
    for line in content.splitlines():
        match = TIMESTAMP_RE.match(line.strip())
        if not match:
            output.append(line)
            continue
        start = _from_milliseconds(_to_milliseconds(match.group("start")) + offset_ms)
        end = _from_milliseconds(_to_milliseconds(match.group("end")) + offset_ms)
        output.append(f"{start} --> {end}")
        shifted += 1
    if not shifted:
        raise ValueError("输入文件没有找到 SRT 时间轴。")
    return "\n".join(output) + "\n"


def _ass_to_milliseconds(value: str) -> int:
    hours, minutes, seconds_and_cs = value.split(":")
    seconds, centiseconds = seconds_and_cs.split(".")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(centiseconds) * 10
    )


def _ass_from_milliseconds(value: int) -> str:
    if value < 0:
        raise ValueError("偏移后的时间不能小于 0:00:00.00")
    centiseconds_total = (value + 5) // 10
    hours, remainder = divmod(centiseconds_total, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def shift_ass(content: str, offset_ms: int) -> str:
    output: list[str] = []
    shifted = 0
    for line in content.splitlines():
        match = ASS_EVENT_RE.match(line)
        if not match:
            output.append(line)
            continue
        start = _ass_from_milliseconds(_ass_to_milliseconds(match.group("start")) + offset_ms)
        end = _ass_from_milliseconds(_ass_to_milliseconds(match.group("end")) + offset_ms)
        output.append(f'{match.group("prefix")}{start},{end}{match.group("suffix")}')
        shifted += 1
    if not shifted:
        raise ValueError("输入文件没有找到 ASS 事件时间轴。")
    return "\n".join(output) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="将裁剪片段 SRT/ASS 的时间轴回填到原视频时间。")
    parser.add_argument("--input", required=True, type=Path, help="片段相对时间 SRT 或 ASS")
    parser.add_argument("--output", required=True, type=Path, help="输出的原视频绝对时间字幕")
    parser.add_argument("--offset", required=True, help="原视频中的片段起点，例如 00:54:00")
    parser.add_argument("--format", choices=("auto", "srt", "ass"), default="auto")
    args = parser.parse_args()

    source = args.input.resolve()
    destination = args.output.resolve()
    normalized_offset = f"{args.offset},000" if "," not in args.offset else args.offset
    offset_ms = _to_milliseconds(normalized_offset)
    subtitle_format = source.suffix.lower().lstrip(".") if args.format == "auto" else args.format
    if subtitle_format not in {"srt", "ass"}:
        raise ValueError("无法根据扩展名判断字幕格式，请使用 --format srt 或 --format ass。")
    shifter = shift_srt if subtitle_format == "srt" else shift_ass
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(shifter(source.read_text(encoding="utf-8-sig"), offset_ms), encoding="utf-8-sig")
    print(f"SHIFTED_{subtitle_format.upper()}={destination}")
    print(f"OFFSET={_from_milliseconds(offset_ms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
