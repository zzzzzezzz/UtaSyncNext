from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.subtitle_policy import resolve_subtitle_layout
from core.translator import SubtitleTranslator
from utils.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real API translation smoke test on an existing SRT.")
    parser.add_argument("--srt", required=True, help="Input SRT path.")
    parser.add_argument("--lyrics", default="", help="Optional reference lyrics text file.")
    parser.add_argument("--media", default="", help="Optional source media path for the real layout policy.")
    parser.add_argument("--mode", choices=["bilingual", "chinese_only"], default="bilingual")
    parser.add_argument("--proxy", default=None, help="Override API proxy. Use 10808 for local port-only proxy.")
    parser.add_argument("--in-place", action="store_true", help="Translate next to the input SRT instead of a temp copy.")
    args = parser.parse_args()

    settings = load_settings()
    api_key = str(settings.get("api_key") or "").strip()
    api_url = str(settings.get("api_url") or "").strip()
    api_model = str(settings.get("api_model") or "").strip()
    if not api_key:
        print("FAIL: user_settings.json does not contain api_key.")
        return 1
    if not api_url or not api_model:
        print("FAIL: user_settings.json is missing api_url or api_model.")
        return 1

    source_srt = Path(args.srt).resolve()
    if not source_srt.exists():
        print(f"FAIL: SRT not found: {source_srt}")
        return 1

    lyrics_path = Path(args.lyrics).resolve() if args.lyrics else None
    media_path = Path(args.media).resolve() if args.media else None
    if media_path is not None and not media_path.is_file():
        print(f"FAIL: media not found: {media_path}")
        return 1
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if args.in_place:
        work_srt = source_srt
    else:
        temp_dir = tempfile.TemporaryDirectory(prefix="utasyncnext_real_translation_")
        work_srt = Path(temp_dir.name) / source_srt.name
        shutil.copy2(source_srt, work_srt)

    if lyrics_path is None:
        cached_lyrics = str(settings.get("cached_lyrics") or "").strip()
        if cached_lyrics:
            lyrics_root = work_srt.parent
            lyrics_path = lyrics_root / "reference_lyrics.txt"
            lyrics_path.write_text(cached_lyrics, encoding="utf-8")

    original_cues = _count_cues(source_srt)
    proxy_url = args.proxy if args.proxy is not None else str(settings.get("proxy_url") or "").strip()
    layout_policy = resolve_subtitle_layout(str(media_path), settings) if media_path else None
    print("REAL_TRANSLATION_CONFIG")
    print(f"api_url={api_url}")
    print(f"api_model={api_model}")
    print(f"api_key_present=True length={len(api_key)}")
    print(f"proxy={proxy_url or '(empty)'}")
    print(f"mode={args.mode}")
    print(f"input_cues={original_cues}")
    print(f"lyrics_content_present={_has_text(lyrics_path)}")
    print(f"layout_profile={layout_policy.profile if layout_policy else 'translator_default'}")
    if layout_policy:
        print(f"layout_limits=ch:{layout_policy.ch_max_units} jp:{layout_policy.jp_max_units}")

    translator = SubtitleTranslator(
        api_key=api_key,
        base_url=api_url,
        primary_model=api_model,
        reasoning_model=api_model,
        srt_path=str(work_srt),
        reference_txt_path=str(lyrics_path) if lyrics_path else None,
        output_mode=args.mode,
        proxy_url=proxy_url,
        layout_profile=layout_policy.profile if layout_policy else "wide_16_9",
        ch_max_units=layout_policy.ch_max_units if layout_policy else 64,
        jp_max_units=layout_policy.jp_max_units if layout_policy else 84,
        context_title=media_path.stem if media_path else source_srt.stem,
    )
    output = translator.run()
    if not output:
        if temp_dir is not None:
            temp_dir.cleanup()
        print("FAIL: translator returned no output.")
        return 1

    output_path = Path(output)
    translated_cues = _count_cues(output_path)
    valid_timeline = _timeline_lines(source_srt) == _timeline_lines(output_path)
    payload_changed = _subtitle_payload(source_srt) != _subtitle_payload(output_path)
    print("REAL_TRANSLATION_RESULT")
    print(f"output={output_path}")
    print(f"translated_cues={translated_cues}")
    print(f"timeline_preserved={valid_timeline}")
    print(f"payload_changed={payload_changed}")

    if translated_cues < max(1, int(original_cues * 0.8)):
        print("FAIL: translated cue count is too low.")
        return 1
    if not valid_timeline:
        print("FAIL: translated SRT did not preserve the original timeline.")
        return 1
    if not payload_changed:
        print("FAIL: translated text is identical to the input; API likely failed and fallback text was used.")
        return 1

    print("PASS")
    if temp_dir is not None:
        print(f"temp_dir_retained={temp_dir.name}")
    return 0


def _count_cues(path: Path) -> int:
    return len([line for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines() if line.strip().isdigit()])


def _has_text(path: Path | None) -> bool:
    return bool(path and path.is_file() and path.read_text(encoding="utf-8-sig", errors="ignore").strip())


def _timeline_lines(path: Path) -> list[str]:
    return [
        line.strip().replace(".", ",")
        for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        if "-->" in line
    ]


def _subtitle_payload(path: Path) -> list[str]:
    payload: list[str] = []
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        payload.append(stripped)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
