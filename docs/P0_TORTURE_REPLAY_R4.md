# P0 Torture / Replay R4 — Domain Event Subject Binding

Status: `DRAFT CANDIDATE / OFFLINE DOMAIN HISTORY VERIFICATION / NO EFFECT`

## Purpose

R3 proved that a replay history can be append-only, ordered, deduplicated and externally head-pinned. R3 deliberately left one domain gap open: a generic ledger can prove that an event contains *some* content-addressed subject without proving that the subject is the exact domain artifact expected for that lifecycle position.

R4 closes that gap for the trading decision lifecycle without turning ContinuityOS into a trading-domain authority.

## Exact lifecycle subjects

```text
CASE_QUALIFIED
  -> exact tradingos.trusted_replay_input.v1 / replay_input_sha256

TWIN_COMMITTED
  -> exact sct.prediction/v3 / prediction_id

DECISION_PACKET
  -> exact tradingos.trade_decision_packet.v1 / packet_sha256

HUMAN_REVEAL
  -> exact tradingos.shadow_human_reveal_receipt.v1 / reveal_sha256

OUTCOME_RECEIPT
  -> exact tradingos.trade_outcome_receipt.v1 / receipt_sha256

RETURN_INTAKE
  -> exact control_return_broker.shadow_intake_receipt.v1 / shadow_intake_sha256
```

The resulting manifest is `tradingos.shadow_domain_subject_manifest.v1`.

## Human reveal becomes first-class

R4 introduces a deterministic no-effect reveal receipt instead of treating the human choice as an untyped value inside later scoring.

The reveal receipt binds:
- exact TradeCase id/hash;
- exact DecisionPacket hash;
- exact SCT prediction id carried by that packet;
- one choice from the frozen option set;
- timezone-aware reveal time;
- reveal cannot precede TradeCase freeze or the SCT commit;
- `write_performed=false`;
- `apply_allowed=false`;
- `execution_authority=NONE`.

The existing outcome receipt is then checked against the exact reveal: same case, packet, choice and decision time. A valid but different outcome artifact is not interchangeable.

## R3 remains generic

ContinuityOS remains a domain-generic history owner. R4 does **not** require ContinuityOS to understand SCT, TradingOS, human choice semantics or market outcomes.

TradingOS consumes:
- the exact R3 history verification digest;
- the exact R3 append candidates;
- the R4 subject manifest;
- the exact domain artifacts.

It returns `bitevo.shadow_domain_history_verification.v1` only when every event subject equals the exact expected artifact digest.

The caller must provide an independently retained `expected_history_verification_sha256`. Rehashing the R3 history and R4 subjects locally cannot silently replace that external expectation.

## Admission binding

R4 also closes an adjacent admission gap.

The generic ContinuityOS admission candidate contains `replay_input_sha256`. A new final receipt, `bitevo.shadow_domain_history_closure.v1`, requires:

```text
admission_candidate_sha256
  == R3 history admission_candidate_sha256

admission.case_binding_sha256
  == R3 history case_binding_sha256

admission.replay_input_sha256
  == R4 CASE_QUALIFIED subject_sha256
```

Therefore a history cannot use one trusted replay input as its CASE_QUALIFIED event while its admission record names another replay input.

## R4 adversarial targets

The R4 torture corpus covers:
- R3-valid history with the wrong Twin subject hash;
- admission replay input different from CASE_QUALIFIED subject;
- cross-case SCT prediction rehashed into a valid prediction object;
- reveal/outcome choice divergence;
- wrong externally retained R3 history digest;
- Return intake from a different transaction;
- no-effect/authority preservation across the final domain-history closure.

## Evidence ceiling

R4 proves exact content-addressed semantic binding relative to the supplied artifacts and independently retained expected R3 digest. It does not create cryptographic identity or prove that a human physically made the reveal without a separate trusted capture/signature/custody mechanism.

Likewise, a correct domain history is evidence, not current truth and not effect authority.

## Fixed P0 ceiling

```text
merge=false
deploy=false
runtime_activation=false
runtime_registration=false
current_truth_apply=false
registry_write=false
ledger_write=false
return_index_write=false
external_message=false
executor_dispatch=false
signal=false
order=false
capital_effect=false
execution_authority=NONE
can_trade=false
capital_permission=DENY
```
