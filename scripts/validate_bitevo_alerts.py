import json
from pathlib import Path


REQUIRED_FIELDS = ["id", "ts", "symbol", "tf", "setup_id", "score", "trigger", "risk"]
REQUIRED_RISK_FIELDS = ["side", "entry", "sl", "tp", "r_multiplies", "size_hint_pct"]


def validate_alert(alert):
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in alert:
            errors.append(f"missing field: {field}")

    trigger = alert.get("trigger", {})
    if alert.get("trigger") is not None and "type" not in trigger:
        errors.append("trigger.type missing")

    risk = alert.get("risk", {})
    if alert.get("risk") is not None:
        for field in REQUIRED_RISK_FIELDS:
            if field not in risk:
                errors.append(f"risk.{field} missing")
    return errors


def iter_alerts(path):
    if path.suffix == ".jsonl":
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if line.strip():
                yield line_number, json.loads(line)
    else:
        yield 1, json.loads(path.read_text(encoding="utf-8-sig"))


def main(path_str):
    path = Path(path_str)
    if not path.exists():
        print(f"File not found: {path}")
        return 2

    invalid = 0
    for line_number, alert in iter_alerts(path):
        errors = validate_alert(alert)
        if errors:
            print(f"[{line_number}] INVALID: {errors}")
            invalid += 1

    print("OK" if invalid == 0 else "FAIL")
    return 0 if invalid == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate BitEvo alert JSON or JSONL.")
    parser.add_argument("path", help="Path to alert .json or .jsonl file")
    args = parser.parse_args()
    raise SystemExit(main(args.path))
