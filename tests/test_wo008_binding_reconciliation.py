from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from tools import wo008_binding_reconciliation as module


ROOT = Path(__file__).resolve().parents[1]
GIT_ROOT = Path(os.environ.get("TRADINGOS_SOURCE_ROOT", ROOT))
LOCK_PATH = ROOT / module.DEFAULT_LOCK
ACCEPTED_REF = "bc2c54b0cc089a89eeee3d5a4a3a44502505f767"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pairs() -> list[dict[str, str]]:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    return module.ordered_binding_pairs(lock)


def test_all_current_worktree_bindings_match_frozen_raw_hashes() -> None:
    records = pairs()

    assert len(records) == 45
    assert all(sha256(ROOT / item["path"]) == item["expected_sha256"] for item in records)


def test_accepted_ref_has_exactly_eight_eol_only_mismatches() -> None:
    mismatches: list[str] = []
    for item in pairs():
        accepted = module.git_blob(GIT_ROOT, ACCEPTED_REF, item["path"])
        current = (ROOT / item["path"]).read_bytes()
        if module.sha256_bytes(accepted or b"") == item["expected_sha256"]:
            continue
        mismatches.append(item["path"])
        assert module.semantic_json_equal(accepted, current)
        assert accepted is not None
        assert accepted.replace(b"\n", b"\r\n") == current

    assert len(mismatches) == 8


def test_eol_bound_files_are_exempt_from_git_text_normalization() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    protected = {
        line.rsplit(" ", 1)[0]
        for line in attributes
        if line and not line.startswith("#") and line.endswith(" -text")
    }
    mismatched_paths = {
        item["path"]
        for item in pairs()
        if module.sha256_bytes(module.git_blob(GIT_ROOT, ACCEPTED_REF, item["path"]) or b"")
        != item["expected_sha256"]
    }

    assert protected == mismatched_paths
