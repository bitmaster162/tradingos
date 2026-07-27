from __future__ import annotations

from pathlib import Path

from tools.active_source_integrity_guard import CURATED_EXTERNAL_FILES, build_lock, check_lock


def seed(root: Path) -> None:
    (root / "tools").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "configs").mkdir(parents=True)
    (root / "tools" / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_runtime.py").write_text("def test_ok(): pass\n", encoding="utf-8")


def test_integrity_guard_accepts_exact_reviewed_tree(tmp_path: Path) -> None:
    seed(tmp_path)
    lock_path = tmp_path / "configs" / "ACTIVE_SOURCE_INTEGRITY_LOCK.json"
    lock = build_lock(tmp_path, "review-1")
    lock_path.write_text("{}", encoding="utf-8")

    report = check_lock(tmp_path, lock, lock_path)

    assert report["decision"] == "active_source_integrity_clean"
    assert report["drift_count"] == 0
    assert report["runtime_boundary"]["research_runner_unblocked_by_integrity"] is True
    assert report["can_trade"] is False


def test_integrity_guard_blocks_changed_added_and_missing_files(tmp_path: Path) -> None:
    seed(tmp_path)
    lock_path = tmp_path / "configs" / "ACTIVE_SOURCE_INTEGRITY_LOCK.json"
    lock = build_lock(tmp_path, "review-2")
    lock_path.write_text("{}", encoding="utf-8")
    (tmp_path / "tools" / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "tools" / "foreign.py").write_text("UNREVIEWED = True\n", encoding="utf-8")
    (tmp_path / "tests" / "test_runtime.py").unlink()

    report = check_lock(tmp_path, lock, lock_path)

    assert report["decision"] == "active_source_integrity_drift_blocked"
    assert report["changed"] == ["tools/runtime.py"]
    assert report["untracked"] == ["tools/foreign.py"]
    assert report["missing"] == ["tests/test_runtime.py"]
    assert report["drift_count"] == 3
    assert report["runtime_boundary"]["research_runner_unblocked_by_integrity"] is False
    assert report["can_trade"] is False


def test_integrity_guard_tracks_explicit_external_runtime_surface(tmp_path: Path) -> None:
    seed(tmp_path)
    external = tmp_path / CURATED_EXTERNAL_FILES[0]
    external.parent.mkdir(parents=True)
    external.write_text("print('reviewed')\n", encoding="utf-8")

    lock = build_lock(tmp_path, "review-external")

    assert CURATED_EXTERNAL_FILES[0] in lock["files"]
