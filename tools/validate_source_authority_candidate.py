from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


WORK_ORDER_ID = "CODEX02-R3-RECOVER-OR-BUILD-TRADINGOS-CANONICAL-SOURCE"
R2_PROPOSAL_COMMIT = "b7c19f05dfa54ed7bd183db424e567992f8645b1"
R2_PROPOSAL_TREE = "47bf102286e63ee5bdb2db0f2eb61f530bf02954"
EXPECTED_BRANCH = "codex02/r3-canonical-source-candidate"
REQUIRED_DOCUMENTS = {
    "SOURCE_AUTHORITY_REGISTRY.json",
    "ACTIVE_RELATIONSHIP_CONTRACT.md",
    "DIVERGENT_WO009_DISPOSITION.md",
    "ADOPTION_PLAN.md",
    "ROLLBACK_PLAN.md",
}
DENIED_EFFECTS = {
    "exchange_connection",
    "orders",
    "wallet_effect",
    "scheduler_effect",
    "service_effect",
    "deployment_effect",
    "active_write",
}
SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("telegram_token", re.compile(rb"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("openai_key", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("aws_access_key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
)


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _expect(
    errors: list[str], condition: bool, code: str, detail: str
) -> None:
    if not condition:
        errors.append(f"{code}: {detail}")


def _paths_overlap(left: Path, right: Path) -> bool:
    left_norm = Path(os.path.normcase(str(left.resolve(strict=False))))
    right_norm = Path(os.path.normcase(str(right.resolve(strict=False))))
    try:
        common = Path(os.path.commonpath((str(left_norm), str(right_norm))))
    except ValueError:
        return False
    return common == left_norm or common == right_norm


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    authority = registry.get("authority", {})
    candidate = registry.get("candidate_repository", {})
    provenance = registry.get("provenance", {})
    active = registry.get("active_relationship", {})
    remote = registry.get("remote_policy", {})
    permissions = registry.get("permissions", {})
    divergent = registry.get("divergent_roots", [])

    _expect(errors, registry.get("schema_version") == 1, "SCHEMA", "expected 1")
    _expect(
        errors,
        registry.get("work_order_id") == WORK_ORDER_ID,
        "WORK_ORDER",
        "unexpected work order",
    )
    _expect(
        errors,
        registry.get("registry_role") == "proposal_only",
        "REGISTRY_ROLE",
        "must remain proposal_only",
    )
    _expect(
        errors,
        authority.get("human_authority") == "ROBERT",
        "HUMAN_AUTHORITY",
        "must be ROBERT",
    )
    _expect(
        errors,
        authority.get("authority_status") == "CANDIDATE_ONLY",
        "AUTHORITY_STATUS",
        "must remain CANDIDATE_ONLY",
    )
    _expect(
        errors,
        authority.get("human_approval_status") == "PENDING",
        "APPROVAL_STATUS",
        "R3 cannot record approval",
    )
    _expect(
        errors,
        authority.get("registered_source_root") is None,
        "REGISTERED_ROOT",
        "must be null before adoption",
    )
    _expect(
        errors,
        authority.get("adoption_permitted") is False,
        "ADOPTION",
        "must be false",
    )
    _expect(
        errors,
        authority.get("self_application") is False,
        "SELF_APPLICATION",
        "must be false",
    )

    _expect(
        errors,
        candidate.get("role") == "ISOLATED_CANDIDATE_ONLY",
        "CANDIDATE_ROLE",
        "unexpected role",
    )
    _expect(
        errors,
        candidate.get("expected_branch") == EXPECTED_BRANCH,
        "BRANCH_POLICY",
        "unexpected branch",
    )
    _expect(
        errors,
        candidate.get("clean_required") is True,
        "CLEAN_POLICY",
        "clean tree must be required",
    )
    _expect(
        errors,
        candidate.get("non_nested_required") is True,
        "NESTING_POLICY",
        "non-nested root must be required",
    )
    _expect(
        errors,
        candidate.get("writable_remotes_allowed") is False,
        "REMOTE_POLICY",
        "writable remotes must be denied",
    )
    _expect(
        errors,
        provenance.get("r2_proposal_commit") == R2_PROPOSAL_COMMIT,
        "R2_COMMIT",
        "R2 proposal commit mismatch",
    )
    _expect(
        errors,
        provenance.get("r2_proposal_tree") == R2_PROPOSAL_TREE,
        "R2_TREE",
        "R2 proposal tree mismatch",
    )
    _expect(
        errors,
        provenance.get("required_parent_commit") == R2_PROPOSAL_COMMIT,
        "PARENT_COMMIT",
        "R3 must be one proposal commit over exact R2",
    )

    _expect(
        errors,
        active.get("role") == "RUNTIME_ONLY",
        "ACTIVE_ROLE",
        "Active must remain runtime-only",
    )
    _expect(
        errors,
        active.get("source_authority") is False,
        "ACTIVE_AUTHORITY",
        "Active cannot be source authority",
    )
    _expect(
        errors,
        active.get("write_policy") == "READ_ONLY",
        "ACTIVE_WRITE",
        "Active must be read-only",
    )
    _expect(
        errors,
        active.get("automatic_sync") is False,
        "ACTIVE_SYNC",
        "automatic sync must be false",
    )
    _expect(
        errors,
        active.get("runtime_wiring") == "NONE",
        "RUNTIME_WIRING",
        "R3 cannot wire runtime",
    )
    _expect(
        errors,
        active.get("deployment_authorized") is False,
        "DEPLOYMENT",
        "R3 cannot authorize deployment",
    )

    _expect(
        errors,
        remote.get("configured_remotes_required") == [],
        "REMOTE_REQUIRED",
        "no remote may be required",
    )
    _expect(
        errors,
        remote.get("writable_remote") == "NONE",
        "WRITABLE_REMOTE",
        "must be NONE",
    )
    _expect(
        errors,
        permissions.get("can_trade") is False,
        "CAN_TRADE",
        "must be false",
    )
    _expect(
        errors,
        permissions.get("capital_permission") == "DENY",
        "CAPITAL",
        "must be DENY",
    )
    for key in sorted(DENIED_EFFECTS):
        _expect(
            errors,
            permissions.get(key) is False,
            f"EFFECT_{key.upper()}",
            "must be false",
        )

    required = set(registry.get("required_documents", []))
    _expect(
        errors,
        required == REQUIRED_DOCUMENTS,
        "REQUIRED_DOCUMENTS",
        "required document set mismatch",
    )
    _expect(
        errors,
        len(divergent) == 1,
        "DIVERGENT_ROOT_COUNT",
        "exactly one bound WO-009 root is required",
    )
    if len(divergent) == 1:
        item = divergent[0]
        _expect(
            errors,
            item.get("authority_status") == "REJECTED",
            "WO009_AUTHORITY",
            "divergent root must be rejected",
        )
        _expect(
            errors,
            item.get("disposition") == "QUARANTINE_NO_MERGE",
            "WO009_DISPOSITION",
            "divergent root must remain quarantined",
        )
        _expect(
            errors,
            item.get("candidate_is_ancestor") is False,
            "WO009_LINEAGE",
            "divergent lineage must be explicit",
        )
    return errors


def parent_git_roots(root: Path) -> list[str]:
    roots: list[str] = []
    current = root.resolve(strict=False).parent
    while current != current.parent:
        if (current / ".git").exists():
            roots.append(str(current))
        current = current.parent
    return roots


def scan_bytes_for_secrets(payload: bytes) -> list[str]:
    return [name for name, pattern in SECRET_PATTERNS if pattern.search(payload)]


def scan_tracked_secrets(root: Path) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for relative in _git(root, "ls-files").splitlines():
        path = root / relative
        if not path.is_file():
            continue
        payload = path.read_bytes()
        for pattern_name in scan_bytes_for_secrets(payload):
            hits.append({"path": relative, "pattern": pattern_name})
    return hits


def validate_repository(root: Path, registry: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve(strict=False)
    errors = validate_registry(registry)
    evidence: dict[str, Any] = {"root": str(root)}

    try:
        top_level = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
        evidence["git_top_level"] = str(top_level)
        _expect(errors, top_level == root.resolve(), "GIT_ROOT", "top level mismatch")

        status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
        evidence["clean"] = not bool(status)
        _expect(errors, not status, "DIRTY_TREE", status or "dirty")

        branch = _git(root, "branch", "--show-current")
        evidence["branch"] = branch
        _expect(errors, branch == EXPECTED_BRANCH, "BRANCH", branch)

        head = _git(root, "rev-parse", "HEAD")
        tree = _git(root, "rev-parse", "HEAD^{tree}")
        parent = _git(root, "rev-parse", "HEAD^")
        parent_tree = _git(root, "rev-parse", "HEAD^^{tree}")
        evidence.update(
            {
                "head": head,
                "tree": tree,
                "parent": parent,
                "parent_tree": parent_tree,
            }
        )
        _expect(errors, parent == R2_PROPOSAL_COMMIT, "LINEAGE_PARENT", parent)
        _expect(errors, parent_tree == R2_PROPOSAL_TREE, "LINEAGE_TREE", parent_tree)

        remotes = [item for item in _git(root, "remote").splitlines() if item]
        evidence["remotes"] = remotes
        _expect(errors, not remotes, "REMOTE_PRESENT", ",".join(remotes))

        nested = parent_git_roots(root)
        evidence["ancestor_git_roots"] = nested
        _expect(errors, not nested, "NESTED_REPOSITORY", ",".join(nested))

        parent_required = Path(
            registry["candidate_repository"]["required_parent_root"]
        ).resolve(strict=False)
        _expect(
            errors,
            root.parent == parent_required,
            "CANDIDATE_PARENT",
            f"{root.parent} != {parent_required}",
        )
        prefix = registry["candidate_repository"]["required_name_prefix"]
        _expect(
            errors,
            root.name.startswith(prefix),
            "CANDIDATE_NAME",
            root.name,
        )

        active = Path(registry["active_relationship"]["path"])
        evidence["active_path_overlap"] = _paths_overlap(root, active)
        _expect(
            errors,
            not evidence["active_path_overlap"],
            "ACTIVE_PATH_OVERLAP",
            f"{root} overlaps {active}",
        )

        proposed = Path(registry["authority"]["proposed_source_root"])
        evidence["proposed_source_root_exists"] = proposed.exists()
        _expect(
            errors,
            not proposed.exists(),
            "ADOPTION_ALREADY_EXISTS",
            str(proposed),
        )

        tracked = set(_git(root, "ls-files").splitlines())
        missing = sorted(REQUIRED_DOCUMENTS - tracked)
        evidence["missing_required_documents"] = missing
        _expect(errors, not missing, "DOCUMENTS_UNTRACKED", ",".join(missing))

        secret_hits = scan_tracked_secrets(root)
        evidence["secret_hits"] = secret_hits
        _expect(errors, not secret_hits, "SECRET_HIT", json.dumps(secret_hits))
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        errors.append(f"VALIDATOR_EXCEPTION: {exc}")

    return {
        "schema_version": 1,
        "work_order_id": WORK_ORDER_ID,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "evidence": evidence,
        "permissions": {
            "self_application": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed validator for the isolated TradingOS R3 candidate."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--registry", type=Path, default=Path("SOURCE_AUTHORITY_REGISTRY.json")
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    root = args.root.resolve(strict=False)
    registry_path = (
        args.registry
        if args.registry.is_absolute()
        else root / args.registry
    )
    try:
        report = validate_repository(root, load_registry(registry_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": 1,
            "work_order_id": WORK_ORDER_ID,
            "status": "FAIL",
            "errors": [f"LOAD_ERROR: {exc}"],
            "evidence": {"root": str(root)},
            "permissions": {
                "self_application": False,
                "can_trade": False,
                "capital_permission": "DENY",
            },
        }

    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
