"""Extract published engine parts and exercise the portable runtime."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parts", type=Path, help="Directory containing engine part ZIPs")
    parser.add_argument("--output", type=Path, required=True, help="Disposable extraction directory")
    args = parser.parse_args()

    parts = sorted(args.parts.resolve().glob("utasyncnext-engine-part-*.zip"))
    if not parts:
        raise SystemExit(f"No engine ZIP parts found in {args.parts}")
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    engine_root = output / ".engine"
    engine_root.mkdir(parents=True)
    for part in parts:
        with zipfile.ZipFile(part) as archive:
            for member in archive.infolist():
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise SystemExit(f"Unsafe archive member: {part.name}:{member.filename}")
            archive.extractall(engine_root)
        print(f"extracted {part.name} ({part.stat().st_size / 1024 / 1024 / 1024:.3f} GiB)")

    required = [
        engine_root / "python-runtime" / "python.exe",
        engine_root / "python-runtime" / "python._pth",
        engine_root / "Lib" / "site-packages" / "torch",
        engine_root / "Lib" / "site-packages" / "faster_whisper",
        engine_root / "Lib" / "site-packages" / "demucs",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Extracted engine is incomplete: " + "; ".join(missing))

    command = [
        str(engine_root / "python-runtime" / "python.exe"),
        "-c",
        "import torch, torchaudio, faster_whisper, demucs; "
        "print('torch=' + torch.__version__); "
        "print('cuda=' + str(torch.cuda.is_available())); print('imports=ok')",
    ]
    completed = subprocess.run(
        command,
        cwd=output,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    print(completed.stdout or "")
    if completed.returncode:
        raise SystemExit(f"portable engine import failed with exit code {completed.returncode}")
    print(f"PASS: extracted {len(parts)} engine parts and imported the four runtime modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
