#!/usr/bin/env python3
"""CLI for the deterministic M2A edge-research infrastructure."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from edge_research.catalog import authorize_outcome_command, derive_readiness, validate_catalog
from edge_research.common import ContractError, atomic_write_json, load_json
from edge_research.decision import decide
from edge_research.duplicate import compare_registry
from edge_research.preregistration import compile_preregistration


def emit(value: dict, out: Path | None) -> None:
    if out:
        atomic_write_json(out, value)
    print(json.dumps(value, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    duplicate = sub.add_parser("duplicate-check")
    duplicate.add_argument("--registry", required=True, type=Path)
    duplicate.add_argument("--candidate", required=True, type=Path)
    duplicate.add_argument("--out", type=Path)

    prereg = sub.add_parser("compile-prereg")
    prereg.add_argument("--input", required=True, type=Path)
    prereg.add_argument("--out-dir", required=True, type=Path)

    catalog = sub.add_parser("validate-catalog")
    catalog.add_argument("--catalog", required=True, type=Path)
    catalog.add_argument("--out", type=Path)

    readiness = sub.add_parser("catalog-gate")
    readiness.add_argument("--catalog", required=True, type=Path)
    readiness.add_argument("--hypothesis", required=True, type=Path)
    readiness.add_argument("--out", type=Path)

    outcome = sub.add_parser("outcome-run")
    outcome.add_argument("--catalog", required=True, type=Path)
    outcome.add_argument("--readiness", required=True, type=Path)
    outcome.add_argument("--prereg", required=True, type=Path)
    outcome.add_argument("--receipt", required=True, type=Path)
    outcome.add_argument("--out", type=Path)

    terminal = sub.add_parser("decide")
    terminal.add_argument("--evidence", required=True, type=Path)
    terminal.add_argument("--out", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "duplicate-check":
            result = compare_registry(load_json(args.candidate), load_json(args.registry))
            emit(result, args.out)
            return 0
        if args.command == "compile-prereg":
            result = compile_preregistration(args.input, args.out_dir)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "validate-catalog":
            result = validate_catalog(load_json(args.catalog))
            emit(result, args.out)
            return 0
        if args.command == "catalog-gate":
            result = derive_readiness(load_json(args.hypothesis), load_json(args.catalog))
            emit(result, args.out)
            return 0
        if args.command == "outcome-run":
            authorization = authorize_outcome_command(
                load_json(args.catalog),
                load_json(args.readiness),
                load_json(args.prereg),
                load_json(args.receipt),
            )
            result = {
                **authorization,
                "status": "OUTCOME_EXECUTION_DEFERRED_TO_M2B",
                "outcomes_computed": False,
                "reason": "M2A contains no market outcome adapter by design",
            }
            emit(result, args.out)
            return 6
        if args.command == "decide":
            result = decide(load_json(args.evidence))
            emit(result, args.out)
            return 0
        raise AssertionError("unreachable command")
    except ContractError as exc:
        result = exc.as_dict()
        if getattr(args, "out", None):
            atomic_write_json(args.out, result)
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
