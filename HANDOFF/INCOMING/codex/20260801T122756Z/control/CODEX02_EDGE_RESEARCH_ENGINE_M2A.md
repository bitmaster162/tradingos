# CODEX-02 — Deterministic Edge Research Engine M2A

TASK_ID: `TRADING_EDGE_RESEARCH_ENGINE_M2A`
SLOT: `CODEX-02`
TASK_CLASS: `RESEARCH_INFRASTRUCTURE_IMPLEMENTATION_ONLY`

## Why this is authorized now

Marathon M1 is physically bound:

- terminal `EDGE_RESEARCH_M1_COMPLETE`;
- ZIP SHA-256
  `d3126e9cd2fc504b98659bb0bf938f2a855d6d2d8a43ca247cac06719602977d`;
- H1 `INSUFFICIENT_DATA`;
- H2 `KILL`;
- H3 `KILL`;
- accepted runtime edges `[]`.

Antigravity is separately building the exact edge-data census. This M2A task
must not duplicate that census and must not compute outcomes before the census
is controller-adjudicated.

## Objective

Build a reusable, deterministic, test-covered historical research engine that
can later consume:

- `EDGE_DATA_CATALOG.json`;
- `EDGE_HYPOTHESIS_DATA_READINESS.json`;
- exact preregistration files;
- immutable source packets.

This task ends with an engine ready for the post-census M2B research run.

## Source and Git gate

Search only:

1. exact current CODEX-02 session/source root;
2. the exact M1 return by the SHA above;
3. `C:\PROJECTS\trading-edge`.

Do not use old R43 TradingOS contract closure as the M2A source unless it is
explicitly classified as a predecessor dependency.

If no clean Git root exists:

1. extract the exact M1 source into a disposable local directory;
2. secret/runtime scan it;
3. initialize a new Git repository;
4. create an import baseline commit before implementation;
5. record provenance for every imported path.

## Phase A — duplicate and family registry

Implement a machine-readable duplicate detector across:

- M1 H1/H2/H3;
- prior cycle-02/cycle-03 hypotheses present in exact evidence;
- the six candidate families in the controller registry.

Required output classes:

- `MATERIAL_DUPLICATE`
- `RENAMED_KILLED_FAMILY`
- `PARTIAL_OVERLAP`
- `MATERIALLY_DISTINCT`
- `INSUFFICIENT_EVIDENCE`

The detector may use structured fields and deterministic text fingerprints. It
must not use an LLM call as the only gate.

## Phase B — preregistration compiler

Implement a CLI that:

- validates the controller preregistration schema;
- canonicalizes JSON;
- computes exact SHA-256;
- freezes train/validation/final-test intervals;
- validates purge/embargo rules;
- validates cost/latency fields;
- validates multiple-testing family and correction;
- rejects mutable or incomplete final-test rules;
- produces a signed/hash-bound `PREREGISTRATION_RECEIPT.json`.

The engine must prevent outcome commands until a valid preregistration receipt
exists.

## Phase C — source catalog gate

Implement validators for future census files:

- source IDs;
- exchange/vendor/symbol/channel;
- timestamp units/timezone;
- min/max time;
- missingness;
- duplicates;
- monotonicity;
- clock skew;
- join coverage;
- freshness;
- provenance;
- immutable raw-byte references.

Map each hypothesis to:

- `DATA_READY`
- `PARTIAL_DATA`
- `NO_DATA`
- `PROVENANCE_BLOCKED`

No outcome command may run for any status except controller-authorized
`DATA_READY`.

## Phase D — deterministic research core

Implement reusable components:

- event extraction interface;
- event clustering/deduplication;
- time-ordered splits;
- purge/embargo;
- anchored or walk-forward OOS;
- fees/spread/slippage/funding/latency costs;
- delayed-entry sensitivity;
- block/stationary bootstrap;
- permutation/placebo tests;
- Holm multiple-testing correction;
- regime/source ablations;
- one-source-removed sensitivity;
- outlier/tail sensitivity;
- reproducible seed/environment capture.

All components must be strategy-neutral and tested with synthetic fixtures.

## Phase E — terminal engine

Implement exact per-hypothesis decisions:

- `KEEP_FOR_FORWARD_PAPER`
- `KILL`
- `INSUFFICIENT_DATA`
- `INVALID_RESEARCH_RETURN`

The software must not call `KEEP_FOR_FORWARD_PAPER` from in-sample or validation
results alone. Final-test evidence is required.

`KEEP_FOR_FORWARD_PAPER` remains a measurement authorization only. It is not an
edge acceptance or trading permission.

## Phase F — adversarial regression

At minimum test:

- renamed M1-H2/H3;
- threshold chosen after final-test read;
- contaminated OOS interval;
- clock-skew artifact;
- missing costs;
- duplicate events inflating sample;
- one event/source dominating;
- placebo matching the claimed result;
- preregistration SHA mismatch;
- source catalog mutation;
- incomplete join coverage;
- outcome command before census authorization;
- deterministic replay equality.

## Phase G — deliverables

Deliver:

- clean Git bundle and source ZIP;
- CLI usage;
- schemas;
- synthetic fixtures;
- exact test suite;
- threat model;
- known limitations;
- post-census continuation contract.

Do **not** compute real hypothesis outcomes in M2A.

## Terminals

- `EDGE_RESEARCH_ENGINE_M2A_READY`
- `EDGE_RESEARCH_ENGINE_M2A_REVISE`
- `EDGE_RESEARCH_M2A_SOURCE_NOT_FOUND`

No market-data download, outcome calculation, strategy implementation,
CODEX-05 handoff, trading effect or successor task.
