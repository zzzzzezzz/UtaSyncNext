from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.translator import SubtitleTranslator


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
ねえ もしも全て投げ捨てられたら

2
00:00:04,000 --> 00:00:06,500
笑って生きることが楽になるの
"""


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    server_version = "UtaSyncFakeOpenAI/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def do_POST(self) -> None:
        if not self.path.endswith("/chat/completions"):
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            self.send_error(400)
            return

        content = _translate_prompt(str(payload.get("messages", [{}])[-1].get("content", "")))
        response = {
            "id": "chatcmpl-utasync-smoke",
            "object": "chat.completion",
            "model": payload.get("model", "fake-model"),
            "created": 1770000000,
            "system_fingerprint": "fp_utasync_smoke",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                }
            ],
        }
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the Next-native translator with a local fake API.")
    parser.add_argument("--mode", choices=["bilingual", "chinese_only"], default="bilingual")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="utasync_translator_") as temp_dir:
        temp_root = Path(temp_dir)
        srt_path = temp_root / "sample_极致打轴.srt"
        lyrics_path = temp_root / "lyrics.txt"
        srt_path.write_text(SAMPLE_SRT, encoding="utf-8")
        lyrics_path.write_text("ねえ、もしも全て投げ捨てられたら\n笑って生きることが楽になるの?", encoding="utf-8")

        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            base_url = f"http://127.0.0.1:{server.server_port}/v1"
            progress_events: list[tuple[float, str]] = []
            translator = SubtitleTranslator(
                api_key="fake-key",
                base_url=base_url,
                primary_model="fake-primary",
                reasoning_model="fake-reasoning",
                srt_path=str(srt_path),
                reference_txt_path=str(lyrics_path),
                output_mode=args.mode,
                proxy_url="",
            )
            output = translator.run(progress_cb=lambda value, text: progress_events.append((value, text)))
        finally:
            server.shutdown()
            server.server_close()

        if not output:
            print("[FAIL] translator returned no output")
            return 1

        output_path = Path(output)
        content = output_path.read_text(encoding="utf-8")
        if "测试翻译 1" not in content or "测试翻译 2" not in content:
            print("[FAIL] translated content missing expected fake lines")
            print(content)
            return 1
        if "00:00:01,000 --> 00:00:03,000" not in content:
            print("[FAIL] original timestamps were not preserved")
            print(content)
            return 1
        if args.mode == "chinese_only" and "原文" in content:
            print("[FAIL] chinese_only output still contains source line")
            print(content)
            return 1
        if not progress_events:
            print("[FAIL] progress callback was not called")
            return 1

        trace_path = srt_path.with_name(f"{srt_path.stem}_api_trace.json")
        if not trace_path.exists():
            print("[FAIL] API trace file was not created")
            return 1
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        responses = [entry for entry in trace.get("calls", []) if entry.get("event") == "response"]
        if trace.get("status") != "completed" or not responses:
            print("[FAIL] API trace did not record a completed response")
            return 1
        if responses[0].get("system_fingerprint") != "fp_utasync_smoke":
            print("[FAIL] API trace did not preserve the system fingerprint")
            return 1
        if responses[0].get("usage", {}).get("total_tokens") != 150:
            print("[FAIL] API trace did not preserve token usage")
            return 1
        if "fake-key" in trace_path.read_text(encoding="utf-8"):
            print("[FAIL] API trace leaked the API key")
            return 1

        print("[PASS] Next-native translator smoke test passed")
        print(f"Output: {output_path}")
        return 0


def _translate_prompt(prompt: str) -> str:
    blocks = _extract_current_batch(prompt)
    translated: list[str] = []
    for pos, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_line = next((line for line in lines if "-->" in line), "")
        if not time_line:
            continue
        index = re.sub(r"\D", "", lines[0]) or str(pos)
        source = "原文 " + index
        translated.append(f"{index}\n{time_line}\n{source}\n测试翻译 {index}")
    return "\n\n".join(translated)


def _extract_current_batch(prompt: str) -> list[str]:
    marker = "【当前批次 SRT】"
    if marker in prompt:
        prompt = prompt.split(marker, 1)[1]
    return [block.strip() for block in re.split(r"\n\s*\n", prompt.strip()) if "-->" in block]


if __name__ == "__main__":
    raise SystemExit(main())
