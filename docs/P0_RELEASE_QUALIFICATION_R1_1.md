# P0 RELEASE QUALIFICATION R1.1 — independent-live-review repair

Status: DRAFT CANDIDATE / SHADOW ONLY / QUALIFIED FOR INDEPENDENT FINAL REVIEW WITH CONDITIONS

R1.1 repairs the two material defects found by TRIAXIS independent final review R1 without changing the frozen R1→R9 implementation input `80d7e24c983529e837daaae49338cf71f9007425`.

## Repairs

1. Control Center authority exact-head CI is now classified from fresh GitHub evidence as `CI_BLOCKED_PRE_JOB`, not historical-success evidence. The fresh partition is exactly seven blocked surfaces and two green surfaces.
2. The qualifier no longer claims that it personally live-read cross-repo state. It uses the narrower `manifest_snapshot_hash_bound=true` and consumes an independently retained TRIAXIS live-review reference.
3. The independent live snapshot is `triaxis.p0_live_crossrepo_snapshot.v1`, retained in TRIAXIS PR #9 at commit `f0fc766de0221076ba7165eb23a03ee993a4ccc1`, snapshot SHA-256 `42d9564b3a8f2f2c00e9ae21d4128fbe09be34c44a9a41848ca8da8a8d7075f1`.

A later fresh GitHub read reports ContinuityOS history PR #94 `mergeable=true`; the earlier independent-review observation of `mergeable=false` is therefore treated as transient GitHub mergeability state. This does not change `merge_ready=false`, which remains fixed by policy and the owner gate.

## Immutable R1.1 evidence

- manifest schema: `bitevo.p0_release_candidate_manifest.v1_1`
- manifest SHA-256: `e0159e7c7fbeb36a353a171ca40c764ae3a700439ed2cce7073001cab4578f96`
- qualification schema: `bitevo.p0_release_qualification_receipt.v1_1`
- qualification SHA-256: `426c5cf16e3e366e727f855186fd8265300fbc44f3370f4ed1354e3cd5d54c9c`

The qualifier requires the external live-snapshot SHA and exact TRIAXIS commit as independent inputs. It does not create their trust and does not become current-truth authority.

## Fresh CI partition bound by R1.1

Green exact-head surfaces:
- SCT PR #91 — `d5901dd186e24c167a50bfca34f0db93b882b3bc`;
- ContinuityOS history PR #94 — `93b4528b59b2ab92d8184598a114faa47446fe2b`.

Pre-job blocked surfaces:
- Control Center authority;
- Control Center P0;
- HANRI P0;
- frozen TradingOS R1→R9 input/wrapper evidence;
- TRIAXIS P0;
- VisionAssist P0;
- Return Broker P0.

`CI_BLOCKED_PRE_JOB` is not a code-test failure and is not a PASS.

## Result

R1.1 yields only:

`P0_RELEASE_CANDIDATE_R1_1_QUALIFIED_FOR_INDEPENDENT_FINAL_REVIEW_WITH_CONDITIONS`

with `decision=HOLD`, `action=WAIT`, and `final_independent_review_required=true`.

It explicitly records:
- `manifest_snapshot_hash_bound=true`;
- `independent_live_review_reference_bound=true`;
- `cross_repo_state_live_read_performed_by_qualifier=false`;
- `production_qualified=false`;
- `release_ready=false`;
- `merge_ready=false`;
- `deploy_ready=false`;
- `runtime_ready=false`;
- `current_truth_promotion_allowed=false`;
- `semantic_acceptance=NOT_PERFORMED`;
- `execution_authority=NONE`;
- `can_trade=false`;
- `capital_permission=DENY`.

## Evidence ceiling

R1.1 is not the final independent review. TRIAXIS must fresh-read the corrected R1.1 package and independently adjudicate it. No release/merge/deploy/runtime authority is created by this qualification.

No merge, deploy, runtime activation, current-truth apply, Human Gate write, lease/receipt/backend write, executor dispatch, signal, order or capital effect is authorized.
