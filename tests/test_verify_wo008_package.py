from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "verify_wo008_package.py"
SPEC = importlib.util.spec_from_file_location("verify_wo008_package", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_verifier_fails_closed_on_incomplete_package(tmp_path: Path) -> None:
    report = MODULE.verify(tmp_path)
    assert report["decision"] == "FAIL"
    assert report["can_trade"] is False
    assert any(item.startswith("missing:") for item in report["failures"])


def test_verifier_accepts_complete_minimal_package(tmp_path: Path) -> None:
    for name in MODULE.REQUIRED:
        if name.endswith(".json"):
            payload = {"decision": "PASS_DIAGNOSIS", "can_trade": False}
            (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
        else:
            (tmp_path / name).write_text("ready\n", encoding="utf-8")
    (tmp_path / "MANIFEST.json").write_text(
        json.dumps({"files": []}), encoding="utf-8"
    )
    report = MODULE.verify(tmp_path)
    assert report["decision"] == "PASS"
    assert report["can_trade"] is False
