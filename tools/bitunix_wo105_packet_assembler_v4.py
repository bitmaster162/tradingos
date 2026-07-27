#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_liquidation_context_v2 as liquidation_v2  # noqa: E402
from tools import bitunix_wo105_packet_assembler_v3 as assembler_v3  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_packet_assembler_v4.py"


def configure_for_v4():
    # Preserve the reviewed V3 packet/event lifecycle and replace only the
    # versioned timestamp adapter. The successor lock binds both file hashes.
    assembler_v3.liquidation = liquidation_v2
    assembler_v3.TOOL_PATH = TOOL_PATH
    return assembler_v3


def main() -> int:
    return configure_for_v4().main()


if __name__ == "__main__":
    raise SystemExit(main())
