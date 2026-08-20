# P0 Torture / Replay R1

Status: `DRAFT CANDIDATE / OFFLINE ADVERSARIAL VERIFICATION / NO EFFECT`

## Scope

Attack the current `bitevo.unified_shadow_closure.v13` federation and the TradingOS ↔ SCT boundary without adding a new authority owner or enabling any runtime/effect path.

The torture suite is deterministic and offline. It does not call models, exchanges, external runtimes, payment systems, messaging systems or executor dispatch.

## Confirmed defects found before hardening

### F1 — cross-case SCT prediction contamination

The prior TradingOS `sct.prediction/v3` consumer validated probabilities and authority but did not require the prediction `case_id` to equal the frozen TradeCase `case_id`.

Impact: a structurally valid prediction from another case could be supplied to the current decision packet.

Correction in candidate branches:

```text
SCT export preserves case_id + arm + options + full prediction hash basis
TradingOS requires twin.case_id == TradeCase.case_id
TradingOS requires arm == sct
TradingOS requires exact option-set/order binding
```

### F2 — SCT preparation freeze mismatch

The prior ContinuityOS Trading shadow adapter accepted a caller-supplied numeric `frozen_at` for A/B/C preparation without proving that it represented the same instant as `TradeCase.frozen_at`.

Impact: the case could be hash-bound while the Twin envelope was frozen to a different time epoch.

Correction in the ContinuityOS candidate branch:

```text
TradeCase.frozen_at must be timezone-aware ISO-8601
numeric SCT frozen_at must be finite
numeric SCT frozen_at must equal TradeCase.frozen_at epoch
mismatch => fail closed
```

### F3 — mutable SCT projection was not independently hash-verifiable downstream

The prior SCT export projected only probabilities/choice/confidence plus minimal metadata. It did not preserve the full body used to derive `Prediction.prediction_id`, and TradingOS did not verify that content hash.

Correction:

```text
export full prediction hash basis
recompute prediction_id at SCT export boundary
recompute prediction_id again at TradingOS ingest
stale/tampered projection => reject
```

This is an integrity check, not a cryptographic signature or proof of ledger custody.

## Torture matrix R1

The dedicated offline suite attacks these paths:

```text
T01 forged TradeCase content with stale case hash
T02 cross-case SCT prediction with internally valid content hash
T03 SCT prediction committed before TradeCase freeze
T04 risk object attempting to smuggle can_trade/capital authority
T05 post-freeze advisory proof attempting frozen-case influence
T06 cognition plane attempting a model call
T07 human interface attempting current-truth write
T08 product/service plane attempting external message
T09 parked Parasite-Killer attempting wallet effect
T10 Executor caller-chosen effect-class bypass
T11 rehashed Executor self-merge capability
T12 rehashed product HOLD -> PASS_SHADOW escalation
```

Expected result for every attack is fail-closed rejection or zero influence.

## Important remaining limitations

### L1 — core market evidence timestamps are not yet first-class in TradeCase v1

`market_evidence.snapshot` and optional `market_evidence.vision` are content-addressed source refs, but the TradeCase v1 ref itself does not carry a mandatory evidence observation timestamp/freshness proof.

The trading-advisory plane already requires `case_relevance_verified` and `pre_freeze_evidence_verified`, but that does not automatically prove temporal admissibility of the core snapshot/vision refs.

Therefore R1 does **not** claim universal pre-freeze proof for all market evidence.

### L2 — SHA-256 content binding is not source authenticity

An attacker able to rewrite an entire artifact and recompute its hash can create a new internally consistent content hash. Parent-child binding prevents local substitution only when at least one expected parent/root hash is obtained from a trusted external authority/custody channel.

P0 therefore continues to rely on externally anchored accepted hashes / Control Center authority / ContinuityOS lineage for authenticity. The torture suite does not convert a self-issued hash into authority.

## Fixed safety ceiling

```text
merge=false
deploy=false
runtime_activation=false
runtime_registration=false
external_message=false
payment_mutation=false
entitlement_mutation=false
current_truth_apply=false
executor_dispatch=false
signal=false
order=false
capital_effect=false
execution_authority=NONE
can_trade=false
capital_permission=DENY
```
