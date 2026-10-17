from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.runtime_installer import fetch_manifest, install_engine


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
        try:
            manifest = fetch_manifest(manifest_path.as_uri())
            install_engine(manifest)
            installed = root / ".engine"
            assert (installed / "Scripts" / "python.exe").read_bytes() == b"fake-python"
            assert (installed / "Lib" / "site-packages" / "faster_whisper" / "__init__.py").exists()
        finally:
            if previous_root is None:
                os.environ.pop("UTASYNCNEXT_APP_ROOT", None)
            else:
                os.environ["UTASYNCNEXT_APP_ROOT"] = previous_root
    print("PASS: runtime installer downloads, verifies, extracts, and installs a local manifest")


if __name__ == "__main__":
    main()
