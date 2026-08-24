from __future__ import annotations

import ast
import copy
from pathlib import Path
import pytest

from r81_shadow_evaluation_fixtures import m, memory_policy, shadow_policy, records, declaration, report, ROOT


def test_duplicate_records_rejected_in_declaration():
    rs = records()
    with pytest.raises(ValueError, match="duplicate retrospective record_id"):
        declaration([rs[0], rs[0]])


def test_duplicate_records_rejected_in_report_even_with_tampered_declaration():
    rs = records(); dec = declaration(rs)
    with pytest.raises(ValueError):
        m.build_shadow_report([rs[0], rs[0]], dec, memory_policy(), shadow_policy())


def test_selective_subset_rejected():
    rs = records(); dec = declaration(rs)
    with pytest.raises(ValueError, match="exactly match"):
        m.build_shadow_report(rs[:-1], dec, memory_policy(), shadow_policy())


def test_undeclared_extra_record_rejected():
    rs = records(); dec = declaration(rs[:-1])
    with pytest.raises(ValueError, match="exactly match"):
        m.build_shadow_report(rs, dec, memory_policy(), shadow_policy())


def test_record_payload_substitution_rejected():
    rs = records(); dec = declaration(rs)
    tampered = copy.deepcopy(rs)
    tampered[0]["claim_outcomes"][0]["outcome"] = "UNRESOLVED"
    with pytest.raises(ValueError):
        m.build_shadow_report(tampered, dec, memory_policy(), shadow_policy())


def test_mixed_or_wrong_memory_policy_record_rejected():
    rs = records(); dec = declaration(rs)
    tampered = copy.deepcopy(rs)
    tampered[0]["memory_policy_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        m.build_shadow_report(tampered, dec, memory_policy(), shadow_policy())


@pytest.mark.parametrize("field", ["record_count", "records_digest", "memory_policy_sha256"])
def test_declaration_binding_mutation_rejected(field):
    dec = declaration()
    if field == "record_count": dec[field] += 1
    else: dec[field] = "0" * 64
    with pytest.raises(ValueError): m.validate_frozen_set_declaration(dec, memory_policy(), shadow_policy())


def test_declaration_record_id_mutation_rejected():
    dec = declaration(); dec["records"][0]["record_id"] = "0" * 24
    with pytest.raises(ValueError): m.validate_frozen_set_declaration(dec, memory_policy(), shadow_policy())


def test_declaration_record_sha_mutation_rejected():
    dec = declaration(); dec["records"][0]["record_sha256"] = "0" * 64
    with pytest.raises(ValueError): m.validate_frozen_set_declaration(dec, memory_policy(), shadow_policy())


def test_declaration_reordering_rejected():
    dec = declaration(); dec["records"] = list(reversed(dec["records"]))
    with pytest.raises(ValueError): m.validate_frozen_set_declaration(dec, memory_policy(), shadow_policy())


def test_declaration_id_tamper_rejected():
    dec = declaration(); dec["declaration_id"] = "0" * 24
    with pytest.raises(ValueError): m.validate_frozen_set_declaration(dec, memory_policy(), shadow_policy())


@pytest.mark.parametrize("field", [
    "frozen_set_declaration_id", "frozen_set_declaration_sha256", "memory_policy_sha256", "shadow_policy_sha256"
])
def test_report_chain_binding_tamper_rejected(field):
    dec = declaration(); rep = report(source_declaration=dec)
    rep[field] = "0" * (24 if field.endswith("_id") else 64)
    with pytest.raises(ValueError): m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


def test_report_record_count_tamper_rejected():
    dec = declaration(); rep = report(source_declaration=dec); rep["record_count"] += 1
    with pytest.raises(ValueError): m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


def test_report_claim_count_tamper_rejected():
    dec = declaration(); rep = report(source_declaration=dec); rep["claim_count"] += 1
    with pytest.raises(ValueError): m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


def test_report_outcome_count_tamper_rejected():
    dec = declaration(); rep = report(source_declaration=dec); rep["counts_by_outcome"]["SUPPORTED"] += 1
    with pytest.raises(ValueError): m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


def test_report_kind_count_tamper_rejected():
    dec = declaration(); rep = report(source_declaration=dec); rep["counts_by_claim_kind"]["BLIND_SPOT"] += 1
    with pytest.raises(ValueError): m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


def test_report_integrity_false_rejected():
    dec = declaration(); rep = report(source_declaration=dec); rep["integrity"]["frozen_set_exact"] = False
    with pytest.raises(ValueError): m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


def test_report_id_tamper_rejected():
    dec = declaration(); rep = report(source_declaration=dec); rep["report_id"] = "0" * 24
    with pytest.raises(ValueError): m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


@pytest.mark.parametrize("extra", [
    "predictive_probability", "success_rate", "confidence_score", "model_ranking",
    "provider_ranking", "pnl", "price", "return_pct", "recommended_model"
])
def test_report_rejects_forbidden_output_backdoor(extra):
    dec = declaration(); rep = report(source_declaration=dec); rep[extra] = None
    with pytest.raises(ValueError, match="key set mismatch"):
        m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


def test_r81_core_imports_have_no_network_db_process_env_or_persistence_surface():
    paths = [
        ROOT / "tools" / "tradingos_shadow_evaluation_common.py",
        ROOT / "tools" / "tradingos_shadow_evaluation_contract.py",
    ]
    forbidden_import_roots = {
        "requests", "httpx", "urllib", "socket", "aiohttp", "openai", "anthropic",
        "subprocess", "sqlite3", "psycopg", "pymongo", "redis", "shelve", "pickle", "os",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert roots.isdisjoint(forbidden_import_roots), (path.name, roots & forbidden_import_roots)


def test_r81_core_contains_no_persistence_learning_provider_or_execution_calls():
    paths = [
        ROOT / "tools" / "tradingos_shadow_evaluation_common.py",
        ROOT / "tools" / "tradingos_shadow_evaluation_contract.py",
    ]
    forbidden_tokens = (
        ".write_text(", ".write_bytes(", "open(", "requests.", "httpx.", "socket.",
        "subprocess.", "getenv(", "environ", "api_key", "secret", "place_order",
        "submit_order", "send_order", "update_weights", "update_prompt", "fit(", "train(",
    )
    body = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)
    for token in forbidden_tokens:
        assert token.lower() not in body
