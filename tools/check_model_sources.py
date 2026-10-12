from __future__ import annotations

import argparse
import sys
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from engine.model_downloader import SOURCE_MIRROR, SOURCE_OFFICIAL  # noqa: E402
from engine.model_registry import MODEL_ORDER  # noqa: E402
from engine.model_source_check import check_model_sources, format_source_check_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe UtaSyncNext model repositories without downloading payloads.")
    parser.add_argument("--model", choices=["all", *MODEL_ORDER], default="all")
    parser.add_argument("--source", choices=["official", "mirror", "all"], default="all")
    parser.add_argument("--proxy", default="", help="Optional proxy, for example 10808 or 127.0.0.1:10808.")
    parser.add_argument("--require-mirror", action="store_true", help="Treat mirror failures as errors.")
    parser.add_argument("--json-report", default="test_logs/model_sources-latest.json")
    args = parser.parse_args()

    models = MODEL_ORDER if args.model == "all" else [args.model]
    sources = {
        "official": [SOURCE_OFFICIAL],
        "mirror": [SOURCE_MIRROR],
        "all": [SOURCE_OFFICIAL, SOURCE_MIRROR],
    }[args.source]
    payload = check_model_sources(
        models=models,
        sources=sources,
        proxy_url=args.proxy,
        require_mirror=args.require_mirror,
        report_path=NEXT_ROOT / args.json_report,
    )
    print(format_source_check_report(payload))
    print(f"报告：{NEXT_ROOT / args.json_report}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
