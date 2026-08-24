from __future__ import annotations

import ast
import copy
import pytest

from r80_retrospective_fixtures import m, cal, memory_policy, chain, record, ROOT


def test_annotation_unknown_claim_rejected():
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation["claim_outcomes"][0]["claim_id"] = "UNKNOWN"
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


def test_annotation_missing_claim_rejected():
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation["claim_outcomes"] = []
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


def test_annotation_duplicate_claim_rejected():
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation["claim_outcomes"].append(copy.deepcopy(annotation["claim_outcomes"][0]))
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


@pytest.mark.parametrize("outcome", ["WIN","LOSS","0.75","PROBABLE","BUY"])
def test_annotation_unapproved_outcome_rejected(outcome):
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation["claim_outcomes"][0]["outcome"] = outcome
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


@pytest.mark.parametrize("extra", ["pnl","return_pct","price","probability","confidence","model_score"])
def test_annotation_extra_metric_backdoor_rejected(extra):
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation[extra] = 1
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


def test_annotation_wrong_request_rejected():
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation["request_id"] = "0" * 24
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


def test_annotation_wrong_brief_rejected():
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation["brief_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


@pytest.mark.parametrize("field", [
    "request_sha256","envelope_sha256","transport_receipt_sha256",
    "response_sha256","memory_policy_sha256","annotation_sha256"
])
def test_record_digest_mutation_rejected(field):
    r = record()
    r[field] = "0" * 64
    with pytest.raises(ValueError):
        m.validate_retrospective_record(r, memory_policy())


@pytest.mark.parametrize("field,value", [
    ("outcome", "CONTRADICTED"),
    ("claim_kind", "THESIS"),
    ("rationale_code", "EVIDENCE_CONFLICT"),
])
def test_record_claim_row_mutation_rejected(field, value):
    r = record()
    r["claim_outcomes"][0][field] = value
    with pytest.raises(ValueError):
        m.validate_retrospective_record(r, memory_policy())


def test_r79_receipt_mismatch_rejected():
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    receipt["response_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


def test_invalid_r78_response_rejected():
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    response["can_trade"] = True
    with pytest.raises(ValueError):
        m.build_retrospective_record(
            request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
            transport_policy=tp,envelope=env,transport_receipt=receipt,
            response=response,annotation=annotation,memory_policy=memory_policy()
        )


def test_summary_rejects_count_tamper():
    s = cal.build_count_summary([record()], memory_policy())
    s["counts_by_kind"]["BLIND_SPOT"]["SUPPORTED"] += 1
    with pytest.raises(ValueError):
        cal.validate_count_summary(s, memory_policy())


def test_summary_rejects_total_tamper():
    s = cal.build_count_summary([record()], memory_policy())
    s["total_outcomes"]["SUPPORTED"] += 1
    with pytest.raises(ValueError):
        cal.validate_count_summary(s, memory_policy())



def test_summary_rejects_record_count_tamper():
    s = cal.build_count_summary([record()], memory_policy())
    s["record_count"] += 1
    with pytest.raises(ValueError):
        cal.validate_count_summary(s, memory_policy())


def test_summary_rejects_summary_id_tamper():
    s = cal.build_count_summary([record()], memory_policy())
    s["summary_id"] = "0" * 24
    with pytest.raises(ValueError):
        cal.validate_count_summary(s, memory_policy())


def test_r80_sources_have_no_network_db_process_or_persistence_imports():
    forbidden = {
        "requests","urllib","httpx","aiohttp","socket","subprocess",
        "openai","anthropic","google","cohere","mistralai",
        "sqlite3","sqlalchemy","redis","boto3"
    }
    for path in sorted((ROOT / "tools").glob("tradingos_retrospective_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden), (path, imported & forbidden)


def test_r80_sources_contain_no_persistence_or_learning_calls():
    forbidden = [
        "open(", ".write(", "sqlite", "insert ", "update weights",
        "fit(", "backward(", "optimizer", "getenv(", "environ["
    ]
    for path in sorted((ROOT / "tools").glob("tradingos_retrospective_*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, (path, token)
