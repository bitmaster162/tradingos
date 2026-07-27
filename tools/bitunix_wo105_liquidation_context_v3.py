#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_liquidation_context as base  # noqa: E402
from tools import bitunix_wo105_liquidation_context_v2 as v2  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_liquidation_context_v3.py"
SOURCE = v2.SOURCE
SCHEMA_VERSION = v2.SCHEMA_VERSION
DEFAULT_MAX_CLOCK_SKEW_MS = v2.DEFAULT_MAX_CLOCK_SKEW_MS
DEFAULT_WINDOW_MS = v2.DEFAULT_WINDOW_MS
DEFAULT_MIN_EVENTS = v2.DEFAULT_MIN_EVENTS
DEFAULT_MIN_NOTIONAL_USD = v2.DEFAULT_MIN_NOTIONAL_USD

# V3 is an interface-only successor. Validation and context semantics remain V2.
load_rows = base.load_rows
validate_row = v2.validate_row
build_context = v2.build_context


def main() -> int:
    return v2.main()


if __name__ == "__main__":
    raise SystemExit(main())
