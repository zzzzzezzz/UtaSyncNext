from __future__ import annotations

import argparse
import os
from getpass import getpass
from pathlib import Path

import boto3
from botocore.config import Config
from boto3.s3.transfer import S3Transfer, TransferConfig


DEFAULT_ACCOUNT_ID = "e61de1fbe8c60f25767687f6fe7a3db7"
DEFAULT_BUCKET = "utasyncnext-assets"
DEFAULT_ASSET_DIR = Path("build/engine_release_v0.1.3")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload UtaSyncNext engine ZIP assets to Cloudflare R2 via S3 multipart upload.")
    parser.add_argument("--account-id", default=os.environ.get("R2_ACCOUNT_ID", DEFAULT_ACCOUNT_ID))
    parser.add_argument("--bucket", default=os.environ.get("R2_BUCKET", DEFAULT_BUCKET))
    parser.add_argument("--asset-dir", type=Path, default=Path(os.environ.get("R2_ASSET_DIR", DEFAULT_ASSET_DIR)))
    parser.add_argument("--prefix", default=os.environ.get("R2_PREFIX", ""), help="Optional object key prefix, without a leading slash")
    args = parser.parse_args()

    access_key = os.environ.get("R2_ACCESS_KEY_ID") or input("R2 Access Key ID: ").strip()
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY") or getpass("R2 Secret Access Key (hidden): ").strip()
    if not access_key or not secret_key:
        raise SystemExit("R2 credentials are required.")

    asset_dir = args.asset_dir.resolve()
    assets = sorted(asset_dir.glob("utasyncnext-engine-part-*.zip"))
    if not assets:
        raise SystemExit(f"No engine ZIP assets found in {asset_dir}")

    endpoint = f"https://{args.account_id}.r2.cloudflarestorage.com"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", proxies={}),
    )
    config = TransferConfig(
        multipart_threshold=64 * 1024 * 1024,
        multipart_chunksize=64 * 1024 * 1024,
        max_concurrency=4,
        use_threads=True,
    )
    transfer = S3Transfer(client, config)
    prefix = args.prefix.strip("/")
    for index, path in enumerate(assets, start=1):
        key = f"{prefix}/{path.name}" if prefix else path.name
        print(f"[{index}/{len(assets)}] uploading {path.name} ({path.stat().st_size:,} bytes)", flush=True)
        transfer.upload_file(
            str(path),
            args.bucket,
            key,
            extra_args={"ContentType": "application/zip"},
        )
        metadata = client.head_object(Bucket=args.bucket, Key=key)
        size = int(metadata.get("ContentLength", -1))
        if size != path.stat().st_size:
            raise RuntimeError(f"Uploaded size mismatch for {key}: {size} != {path.stat().st_size}")
        print(f"    uploaded and verified: {key}", flush=True)
    print("R2 upload completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
