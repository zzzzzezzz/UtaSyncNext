from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from tools.subtitle_reviewer import start_review_server
from ui import output_actions as output_actions_module
from ui.output_actions import OutputActionRunner


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _srt(text: str) -> str:
    return f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n"


def _read_json(url: str) -> dict:
    with urllib.request.urlopen(f"{url}data", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    handles = []
    try:
        with tempfile.TemporaryDirectory(prefix="utasync-review-") as temp_dir:
            root = Path(temp_dir)
            first_media = root / "first.mp3"
            second_media = root / "second.mp3"
            first_source = root / "first_source.srt"
            first_translated = root / "first_bilingual.srt"
            second_source = root / "second_source.srt"
            first_media.write_bytes(b"first-media-content")
            second_media.write_bytes(b"second-media-content")
            first_source.write_text(_srt("最初の原文"), encoding="utf-8")
            first_translated.write_text(_srt("最初の原文\n第一条翻译"), encoding="utf-8")
            second_source.write_text(_srt("二つ目の原文"), encoding="utf-8")

            first = start_review_server(first_media, first_source, first_translated, open_browser=False)
            second = start_review_server(second_media, second_source, None, open_browser=False)
            handles.extend([first, second])

            first_payload = _read_json(first.url)
            second_payload = _read_json(second.url)
            _require(first_payload["media_name"] == "first.mp3", "第一个校对页媒体串线")
            _require(second_payload["media_name"] == "second.mp3", "第二个校对页媒体串线")
            _require(first_payload["translated_name"] == "first_bilingual.srt", "双语字幕未加载")
            _require(second_payload["translated_name"] == "", "原文项目错误出现双语字幕")
            _require(first_payload["source_cues"][0]["text"] == "最初の原文", "原文字幕内容错误")
            _require("第一条翻译" in first_payload["translated_cues"][0]["text"], "翻译字幕内容错误")

            bilingual_only = root / "bilingual_only.srt"
            bilingual_only.write_text(_srt("最初の原文\n第一条翻译") + "\n" + _srt("次の原文\n第二条翻译"), encoding="utf-8")
            bilingual = start_review_server(first_media, bilingual_only, None, open_browser=False)
            handles.append(bilingual)
            bilingual_payload = _read_json(bilingual.url)
            _require(bilingual_payload["derived_bilingual"] is True, "单独双语 SRT 未自动拆成两栏")
            _require(bilingual_payload["source_cues"][0]["text"] == "最初の原文", "双语原文拆分错误")
            _require(bilingual_payload["translated_cues"][0]["text"] == "第一条翻译", "双语中文拆分错误")

            request = urllib.request.Request(f"{first.url}media", headers={"Range": "bytes=0-4"})
            with urllib.request.urlopen(request, timeout=5) as response:
                _require(response.status == 206, "媒体服务未支持拖动所需的 Range 请求")
                _require(response.read() == b"first", "媒体 Range 内容错误")

            app_root_dir = root / "app"
            # Keep punctuation that is legal in downloaded Windows filenames.
            # Output discovery must treat the brackets as literal characters,
            # rather than as glob character-class syntax.
            media = root / "sample [source-id].mp4"
            subtitle_dir = app_root_dir / "output" / media.stem / "subtitles"
            subtitle_dir.mkdir(parents=True)
            media.write_bytes(b"sample-media")
            source = subtitle_dir / f"{media.stem}_极致打轴.srt"
            translated = subtitle_dir / f"{media.stem}_极致打轴_双语字幕.srt"
            source.write_text(_srt("原文"), encoding="utf-8")
            translated.write_text(_srt("原文\n译文"), encoding="utf-8")

            class WorkspaceStub:
                def update_outputs(self, artifacts: dict[str, str]) -> None:
                    self.artifacts = artifacts

            class HostStub:
                media_path = str(media)
                last_outputs: dict[str, str] = {}
                workspace = WorkspaceStub()

                def append_log(self, text: str) -> None:
                    self.log = text

            original_app_root = output_actions_module.app_root
            output_actions_module.app_root = lambda: app_root_dir
            try:
                runner = OutputActionRunner(HostStub())
                artifacts = runner._find_existing_outputs()
            finally:
                output_actions_module.app_root = original_app_root

            _require(Path(artifacts["source_srt"]) == source, "输出发现未识别原文 SRT")
            _require(Path(artifacts["translated_srt"]) == translated, "输出发现未识别双语 SRT")
            _require(Path(artifacts["srt"]) == translated, "输出栏没有优先展示双语 SRT")
    finally:
        for handle in handles:
            handle.close()

    print("PASS: subtitle reviewer loads source/translated cues and keeps projects isolated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
