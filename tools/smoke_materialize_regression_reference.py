from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(dir=NEXT_ROOT / "test_workspace") as temp_dir:
        root = Path(temp_dir)
        source = root / "source-input.srt"
        translated = root / "translated-input.srt"
        output = root / "output"
        source.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nremove exactly\n\n"
            "3\n00:00:05,000 --> 00:00:06,000\nworld\n",
            encoding="utf-8",
        )
        translated.write_text(
            "1\n00:00:11,000 --> 00:00:12,000\nhello\n你好\n\n"
            "2\n00:00:13,000 --> 00:00:14,000\nremove exactly\n精确移除\n\n"
            "3\n00:00:15,000 --> 00:00:16,000\nworld\n世界\n\n"
            "4\n00:00:17,000 --> 00:00:18,000\nextra\n额外\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(NEXT_ROOT / "tools" / "materialize_regression_reference.py"),
                "--source-input",
                str(source),
                "--translated-input",
                str(translated),
                "--output-dir",
                str(output),
                "--source-offset-seconds",
                "10",
                "--drop-source-text",
                "remove exactly",
            ],
            cwd=NEXT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stdout
        source_result = (output / "source.srt").read_text(encoding="utf-8")
        translated_result = (output / "translated.srt").read_text(encoding="utf-8")
        provenance = (output / "REFERENCE.json").read_text(encoding="utf-8")
        assert "remove exactly" not in source_result
        assert "remove exactly" not in translated_result
        assert "extra" not in translated_result
        assert translated_result.count("-->") == 2
        assert "quality_claim" in provenance

    print("[PASS] Regression reference materializer preserves only approved timelines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
