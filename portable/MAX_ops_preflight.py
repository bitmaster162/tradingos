import json
from pathlib import Path


REQ = {
    "ohlcv": [["time"], ["open", "high", "low", "close", "volume"]],
    "oi": [["time"], ["price", "close"], ["open_interest"], ["volume"], ["funding"]],
    "basis": [["time"], ["price", "mark_price", "close"], ["index_price", "spot"], ["funding"]],
}


def normalize_header(raw_header):
    return [item.strip().lower() for item in raw_header if item.strip()]


def has_any(header, choices):
    return any(choice.lower() in header for choice in choices)


def read_header(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        first_line = handle.readline().strip()
    return normalize_header(first_line.split(","))


def check_csv(path_str, groups):
    path = Path(path_str)
    if not path.exists():
        return {"exists": False, "error": "missing", "path": str(path)}
    header = read_header(path)
    missing_groups = [group for group in groups if not has_any(header, group)]
    return {
        "exists": True,
        "path": str(path),
        "header": header,
        "missing_groups": missing_groups,
        "ok": not missing_groups,
    }


def main(config_path, output_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    files = config.get("files", {})
    files_b = config.get("filesB", {})
    checks = {}
    if files.get("ohlcv"):
        checks["primary.ohlcv"] = check_csv(files["ohlcv"], REQ["ohlcv"])
    if files.get("oi"):
        checks["primary.oi"] = check_csv(files["oi"], REQ["oi"])
    if files.get("basis"):
        checks["primary.basis"] = check_csv(files["basis"], REQ["basis"])
    if files_b.get("ohlcv"):
        checks["secondary.ohlcv"] = check_csv(files_b["ohlcv"], REQ["ohlcv"])

    summary = {
        "config": str(config_path),
        "checks": checks,
        "all_ok": all(item.get("ok", False) for item in checks.values()) if checks else False,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate OHLCV/OI/Basis CSV contracts for MAX Pipeline.")
    parser.add_argument("--config", required=True, help="Path to MAX_PIPELINE_CONFIG_SAMPLE.json-like config")
    parser.add_argument("--out", default="_dl/MAX_OPS_PREFLIGHT.json", help="Path to JSON preflight report")
    args = parser.parse_args()
    main(args.config, args.out)
