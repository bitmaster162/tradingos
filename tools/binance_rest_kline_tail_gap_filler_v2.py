#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import binance_rest_kline_tail_gap_filler as base  # noqa: E402


TOOL_PATH = "tools/binance_rest_kline_tail_gap_filler_v2.py"
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_SECONDS = 0.2
FIELDNAMES = base.FIELDNAMES


def write_rows(path: Path, rows: list[dict[str, str]], *, create_backup: bool = True) -> Path | None:
    backup_path: Path | None = None
    if path.exists() and create_backup:
        backup_root = path.parent / "_rest_tail_backup" / base.datetime.now(base.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / path.name
        base.shutil.copy2(path, backup_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if attempt + 1 >= REPLACE_ATTEMPTS:
                    raise
                time.sleep(REPLACE_RETRY_SECONDS * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)
    return backup_path


def _out_prefix(argv: list[str]) -> str:
    try:
        return argv[argv.index("--out-prefix") + 1]
    except (ValueError, IndexError):
        return "docs/BINANCE_REST_KLINE_TAIL_GAP_FILLER_2026-07-02"


def main() -> int:
    base.write_rows = write_rows
    result = base.main()
    report_path = base.resolve_path(_out_prefix(sys.argv[1:])).with_suffix(".json")
    if report_path.is_file():
        report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8-sig"))
        report["tool"] = TOOL_PATH
        report["writer_contract"] = {
            "unique_temp_per_process": True,
            "replace_attempts": REPLACE_ATTEMPTS,
            "permission_retry_only": True,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
