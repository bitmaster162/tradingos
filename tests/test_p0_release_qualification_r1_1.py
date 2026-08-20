#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from tools.p0_release_qualification import sha256_obj
from tools.p0_release_qualification_r1_1 import (
    P0ReleaseQualificationR11Error,
    qualify_p0_release_candidate_r1_1,
)

MANIFEST_PATH = Path("evidence/p0_release_candidate_manifest_r1_1.json")
RECEIPT_PATH = Path("evidence/p0_release_qualification_receipt_r1_1.json")
EXPECTED_MANIFEST_SHA = "e0159e7c7fbeb36a353a171ca40c764ae3a700439ed2cce7073001cab4578f96"
EXPECTED_LIVE_SNAPSHOT_SHA = "42d9564b3a8f2f2c00e9ae21d4128fbe09be34c44a9a41848ca8da8a8d7075f1"
EXPECTED_LIVE_COMMIT = "f0fc766de0221076ba7165eb23a03ee993a4ccc1"
GENERATED_AT = "2026-08-20T07:06:00+07:00"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def rehash(manifest: dict) -> dict:
    candidate = copy.deepcopy(manifest)
    candidate.pop("manifest_sha256", None)
    candidate["manifest_sha256"] = sha256_obj(candidate)
    return candidate


class P0ReleaseQualificationR11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_json(MANIFEST_PATH)
        self.expected_receipt = load_json(RECEIPT_PATH)

    def qualify(self, manifest: dict | None = None, **overrides):
        args = {
            "expected_manifest_sha256": EXPECTED_MANIFEST_SHA,
            "expected_live_snapshot_sha256": EXPECTED_LIVE_SNAPSHOT_SHA,
            "expected_live_snapshot_commit_sha": EXPECTED_LIVE_COMMIT,
            "generated_at": GENERATED_AT,
        }
        args.update(overrides)
        return qualify_p0_release_candidate_r1_1(manifest or self.manifest, **args)

    def test_happy_path_matches_immutable_receipt(self):
        self.assertEqual(self.qualify(), self.expected_receipt)
        self.assertEqual(self.expected_receipt["ci_blocked_surface_count"], 7)
        self.assertEqual(self.expected_receipt["ci_green_surface_count"], 2)
        self.assertFalse(self.expected_receipt["cross_repo_state_live_read_performed_by_qualifier"])
        self.assertTrue(self.expected_receipt["final_independent_review_required"])

    def test_control_center_authority_cannot_be_relabelled_green(self):
        candidate = copy.deepcopy(self.manifest)
        candidate["surfaces"][0]["ci_classification"] = "SUCCESS_EXACT_HEAD"
        candidate = rehash(candidate)
        with self.assertRaises(P0ReleaseQualificationR11Error):
            self.qualify(candidate, expected_manifest_sha256=candidate["manifest_sha256"])

    def test_independent_live_snapshot_digest_is_external(self):
        with self.assertRaises(P0ReleaseQualificationR11Error):
            self.qualify(expected_live_snapshot_sha256="0" * 64)

    def test_independent_live_review_flag_is_required(self):
        candidate = copy.deepcopy(self.manifest)
        candidate["independent_live_review"]["generated_outside_qualifier"] = False
        candidate = rehash(candidate)
        with self.assertRaises(P0ReleaseQualificationR11Error):
            self.qualify(candidate, expected_manifest_sha256=candidate["manifest_sha256"])

    def test_frozen_r1_r9_input_cannot_move(self):
        candidate = copy.deepcopy(self.manifest)
        candidate["qualified_input_parent"]["head_sha"] = "1" * 40
        candidate = rehash(candidate)
        with self.assertRaises(P0ReleaseQualificationR11Error):
            self.qualify(candidate, expected_manifest_sha256=candidate["manifest_sha256"])

    def test_required_condition_cannot_disappear(self):
        candidate = copy.deepcopy(self.manifest)
        candidate["known_conditions"].remove("NO_MERGE_AUTHORIZATION")
        candidate = rehash(candidate)
        with self.assertRaises(P0ReleaseQualificationR11Error):
            self.qualify(candidate, expected_manifest_sha256=candidate["manifest_sha256"])

    def test_effect_authority_cannot_widen(self):
        candidate = copy.deepcopy(self.manifest)
        candidate["surfaces"][3]["effect_authority"] = "TRADING"
        candidate = rehash(candidate)
        with self.assertRaises(P0ReleaseQualificationR11Error):
            self.qualify(candidate, expected_manifest_sha256=candidate["manifest_sha256"])

    def test_manifest_tamper_without_expected_digest_rotation_fails(self):
        candidate = copy.deepcopy(self.manifest)
        candidate["release_semantics"]["action"] = "EXECUTE"
        candidate = rehash(candidate)
        with self.assertRaises(P0ReleaseQualificationR11Error):
            self.qualify(candidate)


if __name__ == "__main__":
    unittest.main()
