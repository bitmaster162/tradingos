#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = {
    "pem_private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "telegram_bot_token": re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "assigned_secret": re.compile(
        r"(?i)(?:api[_-]?secret|secret[_-]?key|private[_-]?key|api[_-]?key)\s*[:=]\s*[\"'](?!your|example|placeholder|none|null)[A-Za-z0-9_\-/+=]{20,}[\"']"
    ),
}
CAN_TRADE_TRUE = re.compile(r"(?i)(?:[\"']?can_trade[\"']?\s*[:=]\s*(?:true|True))")
PRIVATE_WS_URL = re.compile(r"wss://[^\s\"']+/private/?", re.IGNORECASE)


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def inspect(proposal: Path, active_root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    secret_findings: list[dict[str, str]] = []
    boundary_findings: list[dict[str, str]] = []
    files = sorted(path for path in proposal.rglob("*") if path.is_file()) if proposal.exists() else []
    relative_files = [path.relative_to(proposal).as_posix() for path in files]
    limits = policy["limits"]

    if not proposal.is_dir():
        failures.append("proposal_directory_missing")
    for required in policy["required_files"]:
        if required not in relative_files:
            failures.append(f"required_file_missing:{required}")
    if len(files) > int(limits["maximum_files"]):
        failures.append("maximum_file_count_exceeded")
    total_bytes = sum(path.stat().st_size for path in files)
    if total_bytes > int(limits["maximum_total_bytes"]):
        failures.append("maximum_total_bytes_exceeded")

    for path in files:
        rel = path.relative_to(proposal).as_posix()
        if path.is_symlink():
            failures.append(f"symlink_forbidden:{rel}")
        if path.stat().st_size > int(limits["maximum_single_file_bytes"]):
            failures.append(f"maximum_single_file_bytes_exceeded:{rel}")
        if path.name.lower() in {name.lower() for name in policy["forbidden_file_names"]}:
            failures.append(f"forbidden_file_name:{rel}")
        if path.suffix.lower() in {suffix.lower() for suffix in policy["forbidden_suffixes"]}:
            failures.append(f"forbidden_file_suffix:{rel}")
        if path.suffix.lower() not in set(policy["text_extensions"]):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            warnings.append(f"text_decode_failed:{rel}")
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                secret_findings.append({"path": rel, "pattern": name})
        if CAN_TRADE_TRUE.search(text):
            boundary_findings.append({"path": rel, "pattern": "can_trade_true"})
        if PRIVATE_WS_URL.search(text):
            boundary_findings.append({"path": rel, "pattern": "private_websocket_url"})

    if secret_findings:
        failures.append("secret_scan_failed")
    if boundary_findings:
        failures.append("runtime_boundary_scan_failed")

    manifest_path = proposal / "BUNDLE_MANIFEST.json"
    manifest_checks = {"present": manifest_path.exists(), "listed_files": 0, "verified_files": 0, "errors": []}
    target_paths: list[str] = []
    listed_paths: set[str] = set()
    if manifest_path.exists():
        try:
            manifest = load_json(manifest_path)
            entries = manifest.get("files") if isinstance(manifest.get("files"), list) else []
            manifest_checks["listed_files"] = len(entries)
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("path") or not entry.get("sha256"):
                    manifest_checks["errors"].append("malformed_manifest_entry")
                    continue
                rel = str(entry["path"]).replace("\\", "/")
                listed_paths.add(rel)
                candidate = (proposal / rel).resolve()
                try:
                    candidate.relative_to(proposal.resolve())
                except ValueError:
                    manifest_checks["errors"].append(f"path_escape:{rel}")
                    continue
                if not candidate.is_file():
                    manifest_checks["errors"].append(f"missing:{rel}")
                    continue
                if sha256(candidate) != str(entry["sha256"]).lower():
                    manifest_checks["errors"].append(f"hash_mismatch:{rel}")
                    continue
                if int(entry.get("size", candidate.stat().st_size)) != candidate.stat().st_size:
                    manifest_checks["errors"].append(f"size_mismatch:{rel}")
                    continue
                manifest_checks["verified_files"] += 1
                if entry.get("target_path"):
                    target_paths.append(str(entry["target_path"]).replace("\\", "/"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            manifest_checks["errors"].append(f"manifest_read_error:{exc}")
    actual_manifest_scope = {rel for rel in relative_files if rel != "BUNDLE_MANIFEST.json"}
    for rel in sorted(actual_manifest_scope - listed_paths):
        manifest_checks["errors"].append(f"unlisted_file:{rel}")
    for rel in sorted(listed_paths - actual_manifest_scope):
        manifest_checks["errors"].append(f"listed_file_not_present:{rel}")
    if manifest_checks["errors"]:
        failures.append("bundle_manifest_verification_failed")

    overlaps = [target for target in target_paths if (active_root / target).exists()]
    if overlaps:
        failures.append("declared_target_path_overlap")
    if not target_paths:
        warnings.append("no_runtime_target_paths_declared")

    decision = "external_proposal_intake_blocked" if failures else "external_proposal_ready_for_semantic_review"
    return {
        "generated_at": now_iso(),
        "tool": "tools/external_proposal_intake_gate.py",
        "policy_id": policy["policy_id"],
        "proposal": portable(proposal),
        "decision": decision,
        "failures": sorted(set(failures)),
        "warnings": sorted(set(warnings)),
        "files": {"count": len(files), "total_bytes": total_bytes},
        "manifest": manifest_checks,
        "target_paths": target_paths,
        "target_path_overlaps": overlaps,
        "secret_findings": secret_findings,
        "boundary_findings": boundary_findings,
        "runtime_boundary": policy["runtime_boundary"],
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# External Proposal Intake Gate",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Proposal: `{report['proposal']}`",
            f"- Decision: `{report['decision']}`",
            "- Can trade: `false`",
            f"- Files: `{report['files']['count']}` / `{report['files']['total_bytes']}` bytes",
            f"- Manifest verified: `{report['manifest']['verified_files']}/{report['manifest']['listed_files']}`",
            f"- Failures: `{', '.join(report['failures']) or 'none'}`",
            f"- Warnings: `{', '.join(report['warnings']) or 'none'}`",
            "",
            "This gate checks quarantine structure, hashes, obvious secrets and no-trade boundaries only. It never executes proposal code and does not grant runtime integration or trading permission.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed intake gate for external TradingOS proposals")
    parser.add_argument("proposal")
    parser.add_argument("--policy", default="configs/EXTERNAL_PROPOSAL_INTAKE_POLICY.json")
    parser.add_argument("--active-root", default=str(ROOT))
    parser.add_argument("--out-prefix", default="docs/EXTERNAL_PROPOSAL_INTAKE_GATE")
    args = parser.parse_args()

    proposal = resolve(args.proposal)
    policy = load_json(resolve(args.policy))
    report = inspect(proposal, resolve(args.active_root), policy)
    out = resolve(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "failures": report["failures"], "out": portable(out.with_suffix('.json')), "can_trade": False}, ensure_ascii=False))
    return 0 if not report["failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
