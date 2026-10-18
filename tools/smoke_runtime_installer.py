from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine.runtime_installer as runtime_installer
from engine.runtime_installer import EngineInstallError, fetch_manifest, install_engine


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="utasync-runtime-installer-") as raw_root:
        root = Path(raw_root)
        archive = root / "engine-part-1.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr("Scripts/python.exe", b"fake-python")
            zipped.writestr("Lib/site-packages/faster_whisper/__init__.py", b"__version__ = 'test'\n")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": "smoke",
                    "assets": [
                        {
                            "name": archive.name,
                            "url": archive.as_uri(),
                            "sha256": digest,
                            "size": archive.stat().st_size,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        previous_root = os.environ.get("UTASYNCNEXT_APP_ROOT")
        os.environ["UTASYNCNEXT_APP_ROOT"] = str(root)
        original_urlopen = runtime_installer.urllib.request.urlopen

        # The model manager's proxy field must also reach the engine manifest
        # request.  Use a local file URI so the smoke stays offline while still
        # exercising the explicit-opener path.
        proxy_calls: list[str] = []
        original_build_opener = runtime_installer.build_urllib_opener

        class _ProxyForwardingOpener:
            def open(self, request, timeout=0):
                return original_urlopen(request, timeout=timeout)

        def capture_proxy(value: str):
            proxy_calls.append(value)
            return _ProxyForwardingOpener()

        try:
            manifest = fetch_manifest(manifest_path.as_uri())
            install_engine(manifest)
            installed = root / ".engine"
            assert (installed / "Scripts" / "python.exe").read_bytes() == b"fake-python"
            assert (installed / "Lib" / "site-packages" / "faster_whisper" / "__init__.py").exists()
            runtime_installer.build_urllib_opener = capture_proxy
            proxied_manifest = fetch_manifest(manifest_path.as_uri(), proxy_url="10808")
            assert proxied_manifest.version == "smoke"
            proxy_target = root / ".engine.proxy"
            install_engine(proxied_manifest, target_root=proxy_target, proxy_url="10808")
            assert (proxy_target / "Scripts" / "python.exe").read_bytes() == b"fake-python"
            # One opener is built for the manifest and one for the ZIP asset;
            # both must receive the same user-entered proxy value.
            assert proxy_calls == ["10808", "10808"]
        finally:
            runtime_installer.build_urllib_opener = original_build_opener
            if previous_root is None:
                os.environ.pop("UTASYNCNEXT_APP_ROOT", None)
            else:
                os.environ["UTASYNCNEXT_APP_ROOT"] = previous_root

    def missing_manifest(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://example.invalid/manifest.json", 404, "Not Found", {}, None)

    runtime_installer.urllib.request.urlopen = missing_manifest
    try:
        try:
            fetch_manifest("https://example.invalid/manifest.json")
        except EngineInstallError as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("a missing manifest should preserve HTTP status 404")
    finally:
        runtime_installer.urllib.request.urlopen = original_urlopen
    print("PASS: runtime installer downloads, verifies, extracts, and installs a local manifest")


if __name__ == "__main__":
    main()
