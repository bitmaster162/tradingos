# Trading Edge Research Engine M2A

Deterministic, strategy-neutral research infrastructure prepared for a later
controller-authorized M2B run. M2A does not download market data, inspect real
outcomes, implement a strategy, or grant paper/live trading permission.

## Safety boundary

- `can_trade=false`
- `capital_permission=DENY`
- `deploy_permission=DENY`
- `self_application=false`
- M1 terminals remain unchanged: H1 `INSUFFICIENT_DATA`, H2 `KILL`, H3 `KILL`
- only synthetic fixtures are exercised in this return
- `outcome-run` fails before controller-adjudicated `DATA_READY`; even a future
  valid authorization returns `OUTCOME_EXECUTION_DEFERRED_TO_M2B`

## Runtime

Python 3.11+ standard library only. No package installation, exchange key,
network access, database, service, scheduler, or TradingOS runtime is needed.

From this directory:

```powershell
python -m unittest discover -s tests -v
python engine/edge_research_cli.py duplicate-check --registry registry/HYPOTHESIS_FAMILY_REGISTRY.json --candidate fixtures/DUPLICATE_CANDIDATE_RENAMED_M1_H2.json
python engine/edge_research_cli.py compile-prereg --input fixtures/PREREGISTRATION_SYNTHETIC_VALID.json --out-dir examples/prereg
python engine/edge_research_cli.py catalog-gate --catalog fixtures/EDGE_DATA_CATALOG_SYNTHETIC.json --hypothesis examples/prereg/PREREGISTRATION_CANONICAL.json --out examples/READINESS_SYNTHETIC.json
```

See `CLI_USAGE.md`, `THREAT_MODEL.md`, `KNOWN_LIMITATIONS.md`, and
`POST_CENSUS_CONTINUATION_CONTRACT.md` before any successor use.

## Components

- hypothesis-family duplicate detector with killed-family protection
- preregistration validator/compiler with canonical SHA-bound receipt
- immutable source-catalog and readiness gate
- strategy-neutral synthetic research primitives
- exact research terminal engine
- adversarial regression suite

This is research infrastructure, not a trading strategy or accepted edge.
