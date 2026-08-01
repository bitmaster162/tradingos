# CLI usage

Run commands from the M2A proposal root. Every command is local and stdlib-only.

## Duplicate check

```powershell
python engine/edge_research_cli.py duplicate-check `
  --registry registry/HYPOTHESIS_FAMILY_REGISTRY.json `
  --candidate fixtures/DUPLICATE_CANDIDATE_RENAMED_M1_H2.json `
  --out examples/DUPLICATE_CHECK_RENAMED_M1_H2.json
```

Exit `0` means the comparison completed, not that a hypothesis was accepted.
The result class decides whether the candidate may proceed.

## Compile preregistration

```powershell
python engine/edge_research_cli.py compile-prereg `
  --input fixtures/PREREGISTRATION_SYNTHETIC_VALID.json `
  --out-dir examples/prereg
```

This emits canonical JSON and a deterministic integrity receipt. It does not
inspect outcomes.

## Validate catalog and derive readiness

```powershell
python engine/edge_research_cli.py validate-catalog `
  --catalog fixtures/EDGE_DATA_CATALOG_SYNTHETIC.json

python engine/edge_research_cli.py catalog-gate `
  --catalog fixtures/EDGE_DATA_CATALOG_SYNTHETIC.json `
  --hypothesis examples/prereg/PREREGISTRATION_CANONICAL.json `
  --out examples/READINESS_SYNTHETIC.json
```

Synthetic readiness can be `DATA_READY` while its controller adjudication is
still `PENDING` and outcome budget remains `DENY`.

## Outcome gate

```powershell
python engine/edge_research_cli.py outcome-run `
  --catalog fixtures/EDGE_DATA_CATALOG_SYNTHETIC.json `
  --readiness examples/READINESS_SYNTHETIC.json `
  --prereg examples/prereg/PREREGISTRATION_CANONICAL.json `
  --receipt examples/prereg/PREREGISTRATION_RECEIPT.json
```

Expected M2A result: exit `4` before controller authorization. The CLI contains
no market outcome adapter; a valid future task authorization still exits `6`
with `OUTCOME_EXECUTION_DEFERRED_TO_M2B`.

## Tests

```powershell
python -m unittest discover -s tests -v
```
