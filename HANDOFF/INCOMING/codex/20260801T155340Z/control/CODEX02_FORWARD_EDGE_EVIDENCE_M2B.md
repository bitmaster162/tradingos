# CODEX-02 — Forward Edge Evidence Marathon M2B

TASK_ID: `TRADING_EDGE_FORWARD_EVIDENCE_M2B`
SLOT: `CODEX-02`
TASK_CLASS: `PREREGISTERED_FORWARD_EVIDENCE_AND_FALSIFICATION`

## Controller decision

CODEX-02 must not sit idle.

The global edge-data census remains useful for entirely new hypothesis families,
but it is not a reason to stop the three existing, already-frozen research leads
whose exact specifications and forward gates live in the trading research repo.

This task uses the M2A engine and continues only previously frozen candidates.
It does not invent a fourth family.

## Start gate

1. Read `CURRENT_BINDING.json` and `PRIOR_CANDIDATES_LOCK.json`.
2. Locate and verify the exact M2A return ZIP:
   `ea105a63679dc03381c548fe13964c10e7bf4d1f91ee29ce07aad6af466c6567`.
3. Extract/clone the exact M2A Git/source candidate into a disposable local Git
   workspace.
4. Locate the exact trading research repository containing the cited candidate
   reports and capture branch/HEAD/tree/full porcelain.
5. Work on `D:` scratch when available. Do not begin a new dependency install or
   large build when `C:` free is below 25 GiB.
6. If exact predecessor or candidate documents cannot be verified, stop REVISE.
7. Do not wait for Antigravity and do not use the rejected placeholder census.

## Immutable exclusions

- M1-H2 remains `KILL`.
- M1-H3 remains `KILL`.
- M1-H1 remains `INSUFFICIENT_DATA` and is not one of this task's three tracks.
- No killed family may be renamed, recombined or resurrected.
- No strategy/runtime/order implementation.

## Track A — Refined RANGE forward evidence

Verify the exact existing candidate and report bytes:

`range_4h_short_near_high_lb40_edge0.2_rr1x2_h16`

with:

`funding_aligned+spot_confirms+oi_expansion`

Prior repository summary reports:

- full expectancy `+0.358554R`;
- holdout expectancy `+0.626488R`;
- all tested segments positive;
- cost stress `+10bps` expectancy `+0.214236R`;
- observer-only, no paper/live permission.

Required work:

1. Recover the exact original selection report, config and freeze timestamp.
2. Prove no config/threshold/feature changed.
3. Use only bars/events strictly after the original freeze/cutoff.
4. Re-run the observer deterministically over genuinely unseen local data.
5. Preserve every emitted/filtered/no-signal event.
6. Account for fees, funding, spread/slippage and delayed-entry stress exactly once.
7. Use a preregistered sequential evidence rule if the original forward gate is
   absent; hash it before reading outcomes.
8. Return one:
   - `KEEP_FOR_FORWARD_PAPER`
   - `KILL`
   - `INSUFFICIENT_DATA`

Historical holdout results alone cannot produce KEEP.

## Track B — Spot-led continuation fresh-sample extension

Verify exact `HYP-SPOT-LEAD-001` source bytes and original preregistration.

Prior repository summary reports that the best fixed SHORT slice had:

- 43 trades;
- 58.14% winrate;
- `+0.15985R` expectancy;
- `+0.025022R` stress expectancy;
- four stable folds;
- failed the preregistered minimum of 80 trades;
- validation/OOS remained closed.

Required work:

1. Preserve the exact original feature, direction, horizon, costs and minimum
   sample. No retuning.
2. Identify the original data cutoff from the exact report.
3. Append only genuinely later, source-bound observations.
4. Do not count the old 43 as new evidence.
5. Open the next evaluation stage only according to the original contract.
6. If total independent evidence still does not satisfy the original minimum,
   return `INSUFFICIENT_DATA`.
7. If the fixed candidate loses post-cost quality, return `KILL`.
8. `KEEP_FOR_FORWARD_PAPER` requires the exact preregistered rule and fresh
   evidence; it is not trading permission.

## Track C — Continuous liquidation-score forward gate

Verify the exact score lock and evidence-gate files.

Frozen bins:

- q25 `0.426128`
- q50 `1.414128`
- q75 `5.109507`

Frozen evidence gate:

- total resolved >= 30;
- inactive baseline >= 8;
- at least one non-inactive bin >= 8;
- bin expectancy >= `0.05R`;
- delta versus inactive >= `0.10R`;
- bootstrap 10th-percentile delta > 0.

Required work:

1. Verify lock SHA and original creation provenance.
2. Read only forward journal/scoreboard outcomes created after the lock.
3. Do not recompute bins from outcomes.
4. Reconcile duplicate, pending, invalid and resolved events.
5. Apply the frozen evidence gate exactly.
6. Return:
   - `KEEP_FOR_FORWARD_PAPER`
   - `KILL`
   - `INSUFFICIENT_DATA`

A gate pass opens a paper-forward research review only; it does not authorize a
filter, veto, signal, alert, order or live execution.

## Shared evidence requirements

For all tracks capture:

- exact source files and SHA-256;
- original and current data cutoffs;
- timestamp units/timezone/availability semantics;
- event independence and dedupe;
- raw and effective sample counts;
- source freshness and missingness;
- fees, funding, spread/slippage and stress costs;
- exact commands/cwd/exit/duration/stdout/stderr;
- deterministic replay;
- source and worktree purity;
- no-effect and teardown.

Use the M2A engine's preregistration, source-catalog and terminal machinery.
Do not silently bypass a gate because a historical metric looked attractive.

## Decision output

Create `FORWARD_EDGE_DECISION_MATRIX.json` with exactly three rows and one
terminal per track:

- `KEEP_FOR_FORWARD_PAPER`
- `KILL`
- `INSUFFICIENT_DATA`
- `INVALID_RESEARCH_RETURN`

Create a CODEX-05 handoff only for a track that receives
`KEEP_FOR_FORWARD_PAPER`. The handoff is proposal-only and must bind exact
preregistration/data/result hashes.

## Return

Strict ZIP/SHA/READY-last with:

- exact Git identity;
- predecessor verification;
- original candidate documents;
- frozen preregistrations;
- source/data receipts;
- raw command outputs;
- per-track result packs;
- decision matrix;
- optional proposal-only CODEX-05 handoff;
- security/no-effect/teardown;
- manifest.

## Terminal

Exactly one:

- `EDGE_FORWARD_EVIDENCE_M2B_COMPLETE`
- `EDGE_FORWARD_EVIDENCE_M2B_REVISE`
- `EDGE_FORWARD_EVIDENCE_M2B_SOURCE_NOT_FOUND`

No market-data download, final-test retuning, strategy implementation, signal,
order, exchange account, trading, capital, deployment, registry/R63 mutation or
successor task.

can_trade=false
capital_permission=DENY
deploy_permission=DENY
self_application=false
NO_FURTHER_AGENT_WORK=true
