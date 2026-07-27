#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bitunix_wo104_acceptance import load_module


TOOL_PATH = "tools/bitunix_wo105_exact_replay.py"
EXPECTED_FRAME_COUNT = 123
EXPECTED_KINDS = {"ControlAck": 1, "DepthUpdate": 40, "TradeBatch": 82}
EXPECTED = {
    "raw_sha256": "cd3033168c4154711c2aba91877cf8fdc1504510b25b5f8c28dd5191929fe481",
    "index_sha256": "ebe909c55d8746de8bd1c9adf92516a150d760d75c2fc36dfc81196a8241319d",
    "manifest_sha256": "5c84528f72f69d96d1af4e2711701d817ff8fb621289d22a091ce118bbd74baa",
    "reviewed_parser_sha256": "be56205745e65640cb93b6c6a557ca66aefb7a5e29b0859b2298552b043255f0",
    "canonical_parser_sha256": "85159345675fb89a636e8169d9fbc5756e4886e50a4ab6033cfff5bde0ec9a19",
    "v3_sha256": "fb7b78878f889391d9cbe7378de1b7df6fcdfda24b737a890137aefdddb0d159",
}
DEFAULTS = {
    "raw": ROOT / "_dl" / "bitunix_gateb_v2_15s_smoke" / "RAW_FRAMES.jsonl",
    "index": ROOT / "_dl" / "bitunix_gateb_v2_15s_smoke" / "RAW_FRAME_INDEX.jsonl",
    "manifest": ROOT / "_dl" / "bitunix_gateb_v2_15s_smoke" / "PUBLIC_CAPTURE_MANIFEST.json",
    "reviewed_parser": ROOT
    / "HANDOFF"
    / "INCOMING"
    / "claude"
    / "20260713_bitunix_gateB_part2"
    / "reviewed_v2"
    / "public_ws_venue.py",
    "canonical_parser": ROOT
    / "HANDOFF"
    / "INCOMING"
    / "claude"
    / "20260713_bitunix_wo104_canonical"
    / "public_ws_venue.py",
    "v3": ROOT
    / "HANDOFF"
    / "INCOMING"
    / "claude"
    / "20260713_bitunix_wo104_canonical"
    / "SETUP_A_PREREG_V3.json",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("expected_json_object")
    return payload


def nonblank_binary_lines(path: Path) -> list[bytes]:
    return [line.rstrip(b"\r") for line in path.read_bytes().split(b"\n") if line.strip()]


def replay(lines: list[bytes], parser_path: Path, module_name: str) -> dict[str, Any]:
    parser = load_module(parser_path, module_name)
    kinds: Counter[str] = Counter()
    frame_kinds: list[str] = []
    unknown: Counter[str] = Counter()
    decode_failures = 0
    for raw in lines:
        try:
            frame = json.loads(raw.decode("utf-8"))
            if isinstance(frame, str):
                frame = json.loads(frame)
            if not isinstance(frame, dict):
                raise ValueError("frame_not_object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            decode_failures += 1
            frame_kinds.append("DECODE_ERROR")
            continue
        venue_ts = frame.get("ts")
        now_ms = int(venue_ts) if isinstance(venue_ts, (int, float)) and venue_ts > 0 else None
        parse_frame = parser.parse_public_frame
        if "now_ms" in inspect.signature(parse_frame).parameters:
            parsed = parse_frame(frame, now_ms=now_ms)
        else:
            parsed = parse_frame(frame)
        kind = type(parsed).__name__
        kinds[kind] += 1
        frame_kinds.append(kind)
        if kind == "UnknownSchema":
            unknown[str(getattr(parsed, "reason", "unknown"))] += 1
    return {
        "parser": portable(parser_path),
        "parser_sha256": sha256_file(parser_path),
        "frames_total": len(lines),
        "parse_kinds": dict(sorted(kinds.items())),
        "frame_kinds": frame_kinds,
        "decode_failures": decode_failures,
        "unknown_schema": dict(sorted(unknown.items())),
    }


def build_report(
    *,
    raw_path: str | Path = DEFAULTS["raw"],
    index_path: str | Path = DEFAULTS["index"],
    manifest_path: str | Path = DEFAULTS["manifest"],
    reviewed_parser_path: str | Path = DEFAULTS["reviewed_parser"],
    canonical_parser_path: str | Path = DEFAULTS["canonical_parser"],
    v3_path: str | Path = DEFAULTS["v3"],
) -> dict[str, Any]:
    paths = {
        "raw": resolve(raw_path),
        "index": resolve(index_path),
        "manifest": resolve(manifest_path),
        "reviewed_parser": resolve(reviewed_parser_path),
        "canonical_parser": resolve(canonical_parser_path),
        "v3": resolve(v3_path),
    }
    failures: list[str] = []
    receipts: dict[str, Any] = {}
    for name, path in paths.items():
        if not path.is_file():
            failures.append(f"missing:{name}")
            receipts[name] = {"path": portable(path), "exists": False}
            continue
        digest = sha256_file(path)
        expected_key = f"{name}_sha256"
        expected_digest = EXPECTED.get(expected_key)
        receipts[name] = {
            "path": portable(path),
            "exists": True,
            "size": path.stat().st_size,
            "sha256": digest,
            "expected_sha256": expected_digest,
        }
        if expected_digest and digest != expected_digest:
            failures.append(f"hash_mismatch:{name}")

    raw_lines: list[bytes] = []
    index_rows: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    if paths["raw"].is_file():
        raw_lines = nonblank_binary_lines(paths["raw"])
        if len(raw_lines) != EXPECTED_FRAME_COUNT:
            failures.append(f"raw_frame_count:{len(raw_lines)}!={EXPECTED_FRAME_COUNT}")
    if paths["index"].is_file():
        for position, line in enumerate(nonblank_binary_lines(paths["index"]), start=1):
            try:
                row = json.loads(line.decode("utf-8"))
                if not isinstance(row, dict):
                    raise ValueError("index_not_object")
                index_rows.append(row)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                failures.append(f"index_decode:{position}")
        if len(index_rows) != EXPECTED_FRAME_COUNT:
            failures.append(f"index_frame_count:{len(index_rows)}!={EXPECTED_FRAME_COUNT}")
    if paths["manifest"].is_file():
        try:
            manifest = read_object(paths["manifest"])
        except (OSError, json.JSONDecodeError, ValueError):
            failures.append("manifest_decode")

    index_hash_failures: list[int] = []
    index_kind_failures: dict[str, list[int]] = {"reviewed": [], "canonical": []}
    if len(raw_lines) == len(index_rows):
        for position, (raw, row) in enumerate(zip(raw_lines, index_rows, strict=True), start=1):
            if row.get("sha256") != hashlib.sha256(raw).hexdigest():
                index_hash_failures.append(position)
        if index_hash_failures:
            failures.append("index_raw_line_hash_mismatch")

    if manifest:
        if manifest.get("frames_total") != EXPECTED_FRAME_COUNT:
            failures.append("manifest_frame_count_mismatch")
        if manifest.get("raw_frames_sha256") != EXPECTED["raw_sha256"]:
            failures.append("manifest_raw_hash_mismatch")
        if manifest.get("parse_kinds") != EXPECTED_KINDS:
            failures.append("manifest_parse_kinds_mismatch")
        if manifest.get("unknown_schema_ledger") not in ({}, None):
            failures.append("manifest_unknown_schema_nonzero")
        if manifest.get("reconnects") != 0:
            failures.append("manifest_reconnects_nonzero")
        if manifest.get("can_trade") is not False:
            failures.append("manifest_can_trade_not_false")

    replays: dict[str, dict[str, Any]] = {}
    if raw_lines and paths["reviewed_parser"].is_file():
        replays["reviewed_v2"] = replay(raw_lines, paths["reviewed_parser"], "_bitunix_wo105_reviewed_parser")
    if raw_lines and paths["canonical_parser"].is_file():
        replays["canonical"] = replay(raw_lines, paths["canonical_parser"], "_bitunix_wo105_canonical_parser")
    for name, result in replays.items():
        if result["frames_total"] != EXPECTED_FRAME_COUNT:
            failures.append(f"replay_frame_count:{name}")
        if result["parse_kinds"] != EXPECTED_KINDS:
            failures.append(f"replay_parse_kinds:{name}")
        if result["decode_failures"] != 0:
            failures.append(f"replay_decode_failures:{name}")
        if result["unknown_schema"]:
            failures.append(f"replay_unknown_schema:{name}")
        if len(index_rows) == len(result["frame_kinds"]):
            label = "reviewed" if name == "reviewed_v2" else "canonical"
            index_kind_failures[label] = [
                position
                for position, (row, kind) in enumerate(zip(index_rows, result["frame_kinds"], strict=True), start=1)
                if row.get("parse_kind") != kind
            ]
            if index_kind_failures[label]:
                failures.append(f"index_parse_kind_mismatch:{name}")
    if set(replays) != {"reviewed_v2", "canonical"}:
        failures.append("both_parsers_not_replayed")
    elif replays["reviewed_v2"]["frame_kinds"] != replays["canonical"]["frame_kinds"]:
        failures.append("parser_frame_classification_divergence")

    failures = sorted(set(failures))
    passed = not failures
    return {
        "schema_version": 1,
        "generated_at": manifest.get("ended_utc") if manifest else None,
        "proof_time_basis": "source_capture_ended_utc_for_deterministic_replay_receipt",
        "tool": TOOL_PATH,
        "decision": "bitunix_wo105_exact_123_frame_replay_pass" if passed else "bitunix_wo105_exact_replay_hold",
        "public_contract_confirmed": passed,
        "canonical_replay": "PASS" if passed else "HOLD",
        "sample_identity": {
            "required_frames": EXPECTED_FRAME_COUNT,
            "expected_parse_kinds": EXPECTED_KINDS,
            "receipts": receipts,
            "index_raw_hash_mismatch_positions": index_hash_failures,
            "index_parse_kind_mismatch_positions": index_kind_failures,
            "manifest_started_utc": manifest.get("started_utc") if manifest else None,
            "manifest_ended_utc": manifest.get("ended_utc") if manifest else None,
        },
        "replays": replays,
        "v3_binding": receipts.get("v3"),
        "failures": failures,
        "interpretation": (
            "Exact byte-bound replay proof only. It confirms the reviewed 123-frame public parser sample; "
            "it does not evaluate a trading edge or authorize signals, paper entries, orders or capital."
        ),
        "runtime_boundary": {
            "edge_evaluated": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    sample = report["sample_identity"]
    lines = [
        "# Bitunix WO105 Exact 123-Frame Replay",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Canonical replay: `{report['canonical_replay']}`",
        f"- Public contract confirmed: `{str(report['public_contract_confirmed']).lower()}`",
        f"- Required frames: `{sample['required_frames']}`",
        f"- Expected kinds: `{sample['expected_parse_kinds']}`",
        f"- Failures: `{report['failures']}`",
        "- Edge evaluated: `false`",
        "- Signals/orders/capital: `DENY`",
        "- Can trade: `false`",
        "",
        "## Bound Receipts",
        "",
    ]
    for name, receipt in sample["receipts"].items():
        lines.append(f"- `{name}`: `{receipt.get('sha256')}` at `{receipt.get('path')}`")
    lines.extend(
        [
            "",
            "Both the reviewed-v2 and current canonical parsers must classify every unchanged frame identically, ",
            "with zero decode failures and zero unknown schemas. Similar captures are not accepted as substitutes.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict byte-bound replay of the exact Bitunix WO105 123-frame sample")
    parser.add_argument("--raw", default=str(DEFAULTS["raw"]))
    parser.add_argument("--index", default=str(DEFAULTS["index"]))
    parser.add_argument("--manifest", default=str(DEFAULTS["manifest"]))
    parser.add_argument("--reviewed-parser", default=str(DEFAULTS["reviewed_parser"]))
    parser.add_argument("--canonical-parser", default=str(DEFAULTS["canonical_parser"]))
    parser.add_argument("--v3", default=str(DEFAULTS["v3"]))
    parser.add_argument("--out-prefix", default="docs/BITUNIX_WO105_EXACT_REPLAY_2026-07-14")
    args = parser.parse_args()
    report = build_report(
        raw_path=args.raw,
        index_path=args.index,
        manifest_path=args.manifest,
        reviewed_parser_path=args.reviewed_parser,
        canonical_parser_path=args.canonical_parser,
        v3_path=args.v3,
    )
    out = resolve(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failures": report["failures"], "can_trade": False}))
    return 0 if report["canonical_replay"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
