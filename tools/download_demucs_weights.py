from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://dl.fbaipublicfiles.com/demucs"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Demucs weights into the UtaSyncNext local cache.")
    parser.add_argument("--model", default="htdemucs_ft", help="Demucs bag name, default: htdemucs_ft.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Remote Demucs base URL.")
    parser.add_argument("--proxy", default="", help="Optional proxy: 10808 / 127.0.0.1:10808 / http://127.0.0.1:10808")
    parser.add_argument("--dry-run", action="store_true", help="Print required files without downloading.")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    required = resolve_required_weights(args.model)
    checkpoints_dir = torch_checkpoints_dir()
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    print(f"Demucs model: {args.model}")
    print(f"Target cache: {checkpoints_dir}")
    print(f"Base URL: {args.base_url.rstrip('/')}")
    if args.proxy:
        print(f"Proxy: {normalize_proxy(args.proxy)}")
    print("Required files:")
    for rel_path, file_name in required:
        status = "present" if is_non_empty_file(checkpoints_dir / file_name) else "missing"
        print(f"- {file_name} [{status}]")

    if args.dry_run:
        return 0

    opener = build_opener(args.proxy)
    missing = [(rel_path, file_name) for rel_path, file_name in required if not is_non_empty_file(checkpoints_dir / file_name)]
    if not missing:
        print("All Demucs weights are already present.")
        return 0

    for rel_path, file_name in missing:
        url = f"{args.base_url.rstrip('/')}/{rel_path.replace(os.sep, '/')}/{file_name}"
        target = checkpoints_dir / file_name
        print(f"\nDownloading {file_name}")
        print(f"URL: {url}")
        download_file(opener, url, target)

    print("\nDemucs weights are ready.")
    return 0


def resolve_required_weights(model_name: str) -> list[tuple[str, str]]:
    remote_dir = find_demucs_remote_dir()
    bag_path = remote_dir / f"{model_name}.yaml"
    files_path = remote_dir / "files.txt"
    if not bag_path.exists():
        raise SystemExit(f"Demucs model config not found: {bag_path}")
    if not files_path.exists():
        raise SystemExit(f"Demucs files index not found: {files_path}")

    signatures = re.findall(r"['\"]([0-9a-f]{8})['\"]", bag_path.read_text(encoding="utf-8", errors="ignore"))
    if not signatures:
        raise SystemExit(f"No model signatures found in {bag_path}")

    entries: dict[str, tuple[str, str]] = {}
    current_root = ""
    for raw_line in files_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("root:"):
            current_root = line.split(":", 1)[1].strip().strip("/")
            continue
        if not line.endswith(".th") or "-" not in line:
            continue
        signature = line.split("-", 1)[0]
        entries[signature] = (current_root, line)

    missing_signatures = [signature for signature in signatures if signature not in entries]
    if missing_signatures:
        raise SystemExit(f"Could not map Demucs signatures to files: {', '.join(missing_signatures)}")
    return [entries[signature] for signature in signatures]


def find_demucs_remote_dir() -> Path:
    candidates = [
        NEXT_ROOT / ".engine" / "Lib" / "site-packages" / "demucs" / "remote",
        NEXT_ROOT / ".venv" / "Lib" / "site-packages" / "demucs" / "remote",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit("Could not find Demucs package metadata in UtaSyncNext .engine or .venv.")


def torch_checkpoints_dir() -> Path:
    torch_home = os.environ.get("TORCH_HOME", "").strip()
    if torch_home:
        return Path(torch_home) / "hub" / "checkpoints"
    return NEXT_ROOT / ".cache" / "torch" / "hub" / "checkpoints"


def normalize_proxy(proxy: str) -> str:
    value = proxy.strip()
    if not value:
        return ""
    if value.isdigit():
        return f"http://127.0.0.1:{value}"
    if "://" not in value:
        return f"http://{value}"
    return value


def build_opener(proxy: str) -> urllib.request.OpenerDirector:
    proxy_url = normalize_proxy(proxy)
    if not proxy_url:
        return urllib.request.build_opener()
    proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    return urllib.request.build_opener(proxy_handler)


def download_file(opener: urllib.request.OpenerDirector, url: str, target: Path) -> None:
    part = target.with_suffix(target.suffix + ".part")
    resume_from = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "UtaSyncNext/1.0"}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
        print(f"  Resuming from {resume_from / (1024 * 1024):.1f} MB")

    request = urllib.request.Request(url, headers=headers)
    started = time.monotonic()
    try:
        with opener.open(request, timeout=60) as response:
            status = getattr(response, "status", 200)
            if resume_from > 0 and status != 206:
                print("  Server did not accept resume request; restarting this file.")
                resume_from = 0

            content_length = int(response.headers.get("Content-Length") or 0)
            total = parse_content_range_total(response.headers.get("Content-Range", "")) or (
                resume_from + content_length if resume_from > 0 else content_length
            )
            downloaded = resume_from
            last_print = 0.0
            mode = "ab" if resume_from > 0 else "wb"
            with open(part, mode) as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_print >= 1.0:
                        print_progress(downloaded, total, started)
                        last_print = now

        final_size = part.stat().st_size
        print_progress(final_size, total or final_size, started)
        if total and final_size < total:
            raise OSError(f"incomplete download: {final_size} of {total} bytes")
        os.replace(part, target)
    except (OSError, urllib.error.URLError) as exc:
        raise SystemExit(f"Download failed for {target.name}: {exc}") from exc


def parse_content_range_total(value: str) -> int:
    if not value or "/" not in value:
        return 0
    tail = value.rsplit("/", 1)[1].strip()
    if not tail.isdigit():
        return 0
    return int(tail)


def print_progress(downloaded: int, total: int, started: float) -> None:
    elapsed = max(time.monotonic() - started, 0.001)
    speed = downloaded / elapsed / (1024 * 1024)
    if total > 0:
        pct = downloaded / total * 100
        print(f"  {downloaded / (1024 * 1024):.1f}/{total / (1024 * 1024):.1f} MB  {pct:.1f}%  {speed:.2f} MB/s")
    else:
        print(f"  {downloaded / (1024 * 1024):.1f} MB  {speed:.2f} MB/s")


def is_non_empty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
