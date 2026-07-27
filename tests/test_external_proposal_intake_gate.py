import hashlib
import json
from pathlib import Path

from tools.external_proposal_intake_gate import inspect


POLICY = {
    "policy_id": "test",
    "required_files": ["PROVENANCE.md", "CHANGESET.md", "BUNDLE_MANIFEST.json"],
    "limits": {"maximum_files": 20, "maximum_total_bytes": 100000, "maximum_single_file_bytes": 50000},
    "forbidden_file_names": [".env"],
    "forbidden_suffixes": [".pem", ".key"],
    "text_extensions": [".py", ".json", ".md"],
    "runtime_boundary": {"can_trade": False},
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_proposal(root: Path, source: str = "can_trade = False\n") -> None:
    (root / "PROVENANCE.md").write_text("agent: test\n", encoding="utf-8")
    (root / "CHANGESET.md").write_text("proposal only\n", encoding="utf-8")
    (root / "source.py").write_text(source, encoding="utf-8")
    files = []
    for name in ("PROVENANCE.md", "CHANGESET.md", "source.py"):
        path = root / name
        files.append({"path": name, "sha256": digest(path), "size": path.stat().st_size})
    (root / "BUNDLE_MANIFEST.json").write_text(json.dumps({"files": files}), encoding="utf-8")


def test_clean_proposal_is_ready_for_semantic_review(tmp_path: Path):
    build_proposal(tmp_path)

    report = inspect(tmp_path, tmp_path / "active", POLICY)

    assert report["decision"] == "external_proposal_ready_for_semantic_review"
    assert report["manifest"]["verified_files"] == 3
    assert report["can_trade"] is False


def test_secret_and_can_trade_true_fail_closed(tmp_path: Path):
    build_proposal(tmp_path, 'can_trade = True\napi_secret = "abcdefghijklmnopqrstuvwx"\n')

    report = inspect(tmp_path, tmp_path / "active", POLICY)

    assert report["decision"] == "external_proposal_intake_blocked"
    assert "secret_scan_failed" in report["failures"]
    assert "runtime_boundary_scan_failed" in report["failures"]


def test_unlisted_file_fails_manifest_coverage(tmp_path: Path):
    build_proposal(tmp_path)
    (tmp_path / "unlisted.py").write_text("can_trade = False\n", encoding="utf-8")

    report = inspect(tmp_path, tmp_path / "active", POLICY)

    assert report["decision"] == "external_proposal_intake_blocked"
    assert "bundle_manifest_verification_failed" in report["failures"]
    assert "unlisted_file:unlisted.py" in report["manifest"]["errors"]
