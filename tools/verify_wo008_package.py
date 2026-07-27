from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED = {
    "RETURN_RECEIPT.md",
    "STATUS.json",
    "MANIFEST.json",
    "SHA256SUMS.txt",
    "SOURCE_AND_RUNTIME_IDENTITY.json",
    "PIPELINE_GRAPH.json",
    "PATH_AND_SCHEMA_BINDING.json",
    "OFFLINE_FAILURE_REPRODUCTION.json",
    "ROOT_CAUSE_VERDICT.md",
    "GIT_BASELINE_RECEIPT.json",
    "REPAIR_DECISION.json",
    "NO_RUNTIME_EFFECT_PROOF.json",
    "WO007_REPORTING_DEFECT_CORRECTION.json",
    "READY_FOR_GPT_REVIEW.flag",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(root: Path) -> dict[str, object]:
    failures: list[str] = []
    missing = sorted(name for name in REQUIRED if not (root / name).is_file())
    failures.extend(f"missing:{name}" for name in missing)

    manifest_path = root / "MANIFEST.json"
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest.get("files", []):
            relative = item["path"]
            path = root / Path(relative)
            if not path.is_file():
                failures.append(f"manifest_missing:{relative}")
                continue
            if path.stat().st_size != item["size"]:
                failures.append(f"manifest_size:{relative}")
            if sha256(path) != item["sha256"]:
                failures.append(f"manifest_hash:{relative}")

    status_path = root / "STATUS.json"
    if status_path.is_file():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("decision") != "PASS_DIAGNOSIS":
            failures.append("status_decision_not_pass_diagnosis")
        if status.get("can_trade") is not False:
            failures.append("status_can_trade_not_false")

    ready_path = root / "READY_FOR_GPT_REVIEW.flag"
    if ready_path.is_file() and ready_path.stat().st_size == 0:
        failures.append("ready_flag_empty")

    return {
        "schema_version": 1,
        "decision": "PASS" if not failures else "FAIL",
        "root": str(root.resolve()),
        "manifest_entries": len(manifest.get("files", [])),
        "failures": failures,
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a WO-008 return directory")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    report = verify(args.root)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
