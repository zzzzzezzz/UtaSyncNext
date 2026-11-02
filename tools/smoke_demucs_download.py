"""Exercise Demucs checkpoint downloads through a local HTTP server only."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.demucs_weights import (
    DemucsWeightsError,
    download_demucs_weights,
    find_demucs_remote_dir,
    inspect_demucs_weights,
)


def expect_download_error(callback, expected: str) -> None:
    try:
        callback()
    except DemucsWeightsError as exc:
        if expected not in str(exc):
            raise AssertionError(f"unexpected download error: {exc}") from exc
    else:
        raise AssertionError(f"download unexpectedly succeeded; expected {expected}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="utasync-demucs-smoke-") as tmp:
        temp_root = Path(tmp)
        engine_root = temp_root / "external-engine"
        site_packages = engine_root / "Lib" / "site-packages"
        remote_dir = site_packages / "demucs" / "remote"
        remote_dir.mkdir(parents=True)
        for module in ("demucs", "faster_whisper", "torch", "torchaudio"):
            package = site_packages / module
            package.mkdir(exist_ok=True)
            (package / "__init__.py").write_text("", encoding="utf-8")

        payloads: dict[str, bytes] = {}
        signatures = [f"{index:08x}" for index in range(1, 5)]
        names = []
        for index, signature in enumerate(signatures):
            # The first file is large enough to preserve one full read when
            # the server drops the first connection partway through.
            payload = bytes([index + 17]) * ((3 if index == 0 else 1) * 1024 * 1024)
            digest = hashlib.sha256(payload).hexdigest()[:8]
            name = f"{signature}-{digest}.th"
            payloads[name] = payload
            names.append(name)

        (remote_dir / "htdemucs_ft.yaml").write_text(
            "models:\n" + "".join(f"  - '{signature}'\n" for signature in signatures),
            encoding="utf-8",
        )
        (remote_dir / "files.txt").write_text(
            "root: mdx_extra\n" + "\n".join(names) + "\n",
            encoding="utf-8",
        )

        requests: list[tuple[str, str | None]] = []
        fail_first = {"value": True}
        corrupt_second = {"value": True}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlsplit(self.path).path
                name = path.rsplit("/", 1)[-1]
                if not path.startswith("/demucs/mdx_extra/") or name not in payloads:
                    self.send_error(404)
                    return
                range_header = self.headers.get("Range")
                requests.append((name, range_header))
                payload = payloads[name]
                if name == names[1] and corrupt_second["value"]:
                    corrupt_second["value"] = False
                    payload = bytes([payload[0] ^ 0xFF]) + payload[1:]
                offset = int(range_header.removeprefix("bytes=").removesuffix("-")) if range_header else 0
                body = payload[offset:]
                self.send_response(206 if range_header else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                if range_header:
                    self.send_header("Content-Range", f"bytes {offset}-{len(payload) - 1}/{len(payload)}")
                self.end_headers()
                if name == names[0] and fail_first["value"]:
                    fail_first["value"] = False
                    self.wfile.write(body[: 1024 * 1024 + 128 * 1024])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            env = {
                "UTASYNCNEXT_ENGINE_ROOT": str(engine_root),
                "TORCH_HOME": str(temp_root / "torch-cache"),
            }
            with patch.dict(os.environ, env):
                if find_demucs_remote_dir() != remote_dir:
                    raise AssertionError("external UTASYNCNEXT_ENGINE_ROOT was not used")
                cache = Path(env["TORCH_HOME"]) / "hub" / "checkpoints"
                cache.mkdir(parents=True)
                (cache / names[2]).write_bytes(b"corrupted local checkpoint")
                base_url = f"http://127.0.0.1:{server.server_port}/demucs"
                ratios: list[float] = []

                def run_download():
                    return download_demucs_weights(
                        base_url=base_url,
                        progress_cb=lambda _message, ratio: ratios.append(ratio),
                    )

                initial = inspect_demucs_weights()
                if len(initial.required_files) != 4 or len(initial.missing_files) != 4:
                    raise AssertionError(f"expected four missing/corrupt weights: {initial}")

                expect_download_error(run_download, "下载 Demucs 权重失败")
                part = (cache / names[0]).with_suffix(".th.part")
                if not part.exists() or part.stat().st_size < 1024 * 1024:
                    raise AssertionError("interrupted download did not leave a resumable .part file")
                resume_size = part.stat().st_size

                expect_download_error(run_download, "校验失败")
                if (cache / names[1]).with_suffix(".th.part").exists():
                    raise AssertionError("invalid downloaded content was retained")

                final = run_download()
                if not final.ready or final.missing_files:
                    raise AssertionError(f"weights did not become ready: {final}")
                for name, expected in payloads.items():
                    if (cache / name).read_bytes() != expected:
                        raise AssertionError(f"checkpoint content mismatch: {name}")
                if (names[0], f"bytes={resume_size}-") not in requests:
                    raise AssertionError(f"Range resume not used: {requests}")
                if sum(name == names[2] for name, _range in requests) != 1:
                    raise AssertionError("corrupted local checkpoint was not re-downloaded")
                if not ratios or ratios[-1] != 1.0 or any(not 0 <= ratio <= 1 for ratio in ratios):
                    raise AssertionError(f"invalid progress ratios: {ratios}")
                request_count = len(requests)
                if not run_download().ready or len(requests) != request_count:
                    raise AssertionError("ready weights unexpectedly triggered a new download")
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

    print("PASS: external engine metadata, interrupted Range resume, SHA rejection, corrupt-file repair")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
