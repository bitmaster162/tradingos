#!/usr/bin/env python3
"""
run_max_pipeline_wrapper_stub.py
Portable wrapper that calls tools.pipeline_runner if the MAX KIT is available.
"""

import importlib
import json
import sys
from pathlib import Path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run MAX Pipeline through tools.pipeline_runner when available.")
    parser.add_argument("--config", required=True, help="Path to MAX_PIPELINE_CONFIG_SAMPLE.json-like file")
    parser.add_argument("--out-prefix", required=True, help="Prefix for LAST_COMPOSITE outputs")
    args = parser.parse_args()

    package_root = Path(__file__).resolve().parents[1]
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

    try:
        pipeline_runner = importlib.import_module("tools.pipeline_runner")
    except Exception:
        print("MAX Pipeline modules not found in this environment.")
        print("Place your KIT so that 'tools.pipeline_runner' is importable and rerun.")
        print(f"Config: {args.config}")
        print(f"Out prefix: {args.out_prefix}")
        return 2

    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    pair = config.get("pair")
    files = config.get("files", {})
    pair_b = config.get("pairB")
    files_b = config.get("filesB", {})
    timeframes = config.get("timeframes", [])

    results = {}
    for timeframe in timeframes:
        print(f"[wrapper] running {pair} {timeframe} ...")
        results[timeframe] = pipeline_runner.run_pipeline(
            pair,
            files,
            tf=timeframe,
            pairB=pair_b,
            filesB=files_b,
        )

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_out = str(out_prefix) + ".json"
    md_out = str(out_prefix) + ".md"
    pipeline_runner.build_report(results, json_out, md_out)
    print(f"[wrapper] wrote {json_out} and {md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
