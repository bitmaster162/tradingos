#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def parse_flags(raw: str) -> int:
    if not raw:
        return 0
    flags = 0
    for chunk in raw.split("|"):
        part = chunk.strip().upper()
        if part == "IGNORECASE":
            flags |= re.IGNORECASE
        elif part == "MULTILINE":
            flags |= re.MULTILINE
        elif part == "DOTALL":
            flags |= re.DOTALL
    return flags


def load_rules(path: Path) -> list[tuple[dict[str, object], re.Pattern[str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    compiled: list[tuple[dict[str, object], re.Pattern[str]]] = []
    for rule in payload:
        compiled.append((rule, re.compile(rule["pattern"], parse_flags(str(rule.get("flags", ""))))))
    return compiled


def scan_text(text: str, rules: list[tuple[dict[str, object], re.Pattern[str]]]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        for rule, pattern in rules:
            for match in pattern.finditer(line):
                hits.append(
                    {
                        "rule_id": rule["id"],
                        "category": rule["category"],
                        "line": line_no,
                        "match": match.group(0),
                        "context": line.strip(),
                    }
                )
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan text with regex alert rules and emit JSON hits.")
    parser.add_argument("text_file", help="Path to the text file to scan.")
    parser.add_argument(
        "--rules",
        default=str(Path(__file__).with_name("alerts_rules.json")),
        help="Path to alerts_rules.json. Defaults to the file next to this script.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args()

    text_path = Path(args.text_file)
    rules_path = Path(args.rules)
    text = text_path.read_text(encoding="utf-8")
    hits = scan_text(text, load_rules(rules_path))
    if args.pretty:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(hits, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
