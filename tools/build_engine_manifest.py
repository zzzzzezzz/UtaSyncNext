from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a SHA256 manifest for split UtaSyncNext engine ZIP assets.")
    parser.add_argument("asset_dir", type=Path, help="Directory containing engine ZIP assets")
    parser.add_argument("output", type=Path, help="Manifest JSON path")
    parser.add_argument("--version", required=True, help="Engine runtime version")
    parser.add_argument("--base-url", required=True, help="Public URL prefix for assets")
    args = parser.parse_args()

    asset_dir = args.asset_dir.resolve()
    assets = []
    for path in sorted(asset_dir.glob("*.zip")):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        assets.append(
            {
                "name": path.name,
                "url": f"{args.base_url.rstrip('/')}/{path.name}",
                "size": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    if not assets:
        raise SystemExit(f"No ZIP assets found in {asset_dir}")

    payload = {
        "version": args.version,
        "total_size": sum(item["size"] for item in assets),
        "assets": assets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} with {len(assets)} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
