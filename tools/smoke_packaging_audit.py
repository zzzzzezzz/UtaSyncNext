from __future__ import annotations

import tempfile
import sys
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from tools.check_packaging_size import audit_dist


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="utasyncnext-packaging-audit-") as temp_dir:
        dist = Path(temp_dir) / "UtaSyncNext"
        dist.mkdir()
        (dist / "UtaSyncNext.exe").write_bytes(b"launcher")

        empty_model_mount = dist / "models_faster"
        empty_model_mount.mkdir()
        clean_result = audit_dist(dist)
        require(
            not clean_result.heavy_hits,
            "an empty external-runtime mount point must not be reported as bundled model payload",
        )

        # A real payload accidentally collected by PyInstaller would sit
        # inside the bundled _internal tree. The top-level models_faster root
        # is intentionally external runtime state and is ignored by the audit.
        leaked_model = dist / "_internal" / "models_faster" / "snapshot" / "model.bin"
        leaked_model.parent.mkdir(parents=True)
        leaked_model.write_bytes(b"model payload")
        model_result = audit_dist(dist)
        require(leaked_model in model_result.heavy_hits, "a model file under models_faster was not detected")

        leaked_model.unlink()
        torch_module = dist / "_internal" / "torch" / "__init__.py"
        torch_module.parent.mkdir(parents=True)
        torch_module.write_text("# bundled heavy module\n", encoding="utf-8")
        module_result = audit_dist(dist)
        require(torch_module in module_result.heavy_hits, "a bundled torch module was not detected")

        torch_module.unlink()
        weight_file = dist / "_internal" / "accidental-weight.safetensors"
        weight_file.write_bytes(b"weight payload")
        suffix_result = audit_dist(dist)
        require(weight_file in suffix_result.heavy_hits, "a model-weight suffix was not detected")

    print("PASS: packaging audit ignores empty runtime mount points and detects real heavy payloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
