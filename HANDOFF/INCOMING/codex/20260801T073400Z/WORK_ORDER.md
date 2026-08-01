# CODEX-02 — Trading Edge Research Marathon M1

TASK_CLASS: PREREGISTERED_RESEARCH_AND_FALSIFICATION
NO_TRADING
NO_STRATEGY_PROMOTION_WITHOUT_DATA

## Global invariants

1. Read `CURRENT_RETURN_REGISTRY.json` first. Never globally search for a return
   unless the exact registry entry is missing.
2. Do not reread the full Control canter archive.
3. Do not rerun completed work merely because an artifact is hard to locate.
4. Persistent software work requires an exact Git baseline:
   repository root, branch, HEAD, tree, clean porcelain or a disposable clean
   clone/worktree from the exact commit.
5. Do not mutate live/runtime roots. Work only in disposable clones or explicit
   candidate branches.
6. Return strict ZIP + SHA-256 sidecar + READY written last.
7. Include exact commands, stdout/stderr, tests, Git identities, diff inventory,
   no-effect receipt and teardown.
8. Do not create successor work orders.
9. `NO_FURTHER_AGENT_WORK=true`
10. `can_trade=false`
11. `capital_permission=DENY`
12. `deploy_permission=DENY`
13. `self_application=false`


## Role boundary

CODEX-02 owns hypothesis discovery/falsification only.
CODEX-05 owns accepted measurement/runtime implementation.

## Goal

Evaluate at most **three** preregistered BTC/USDT edge hypotheses using existing
paper/read-only data.

Each hypothesis must define before observation:

- exact hypothesis ID;
- market/timeframe;
- entry condition;
- invalidation;
- observation horizon;
- transaction-cost model;
- required data;
- primary metric;
- kill criterion;
- keep criterion;
- leakage controls.

## Required candidates

Select no more than three from the currently supported families:

- pressure / OI / CVD dislocation;
- SFP + SMT + local trigger;
- regime-gated divergence continuation;
- or return `INSUFFICIENT_DATA` if none can be tested honestly.

## Evidence

For each hypothesis:

- three independent timestamp-locked observations when data permits;
- raw source identity and freshness;
- no hindsight edits;
- all rejected hypotheses preserved;
- results classified exactly:
  - `KEEP_FOR_FORWARD_PAPER`
  - `KILL`
  - `INSUFFICIENT_DATA`

No universal threshold may be invented.

## Output

- preregistration JSON;
- observation ledger;
- cost/slippage model;
- raw evidence locators and hashes;
- falsification report;
- zero-trade/no-effect receipt.

Terminal:
`EDGE_RESEARCH_M1_COMPLETE`
