# R98 strict OOS open-once protocol

Status: isolated candidate. No live or paper runtime change.

Baseline: f4ccbe4228dc7aa59a0d36f41bab45b5f0a1dce6
Branch: agent/r98-strict-oos-open-once-r1

## Finding

Both nested-holdout tools previously evaluated the OOS window for every grid configuration.
Relative strength can test up to 1200 configurations; session compression can test up to
500 configurations per interval. Their result sort keys also included OOS expectancy.

Therefore labels such as reject_validation_gate_failed_oos_unopened were not literally true:
OOS had already been computed. The report could expose and rank many OOS outcomes.

## Candidate protocol

1. Run the grid on train and validation only.
2. Apply train and validation gates.
3. Rank validation-qualified configurations using train/validation fields only.
4. Freeze exactly one validation winner.
5. Open OOS once for that frozen strategy id.
6. Never re-sort the grid using OOS.
7. Keep every other row OOS status UNOPENED.
8. Report oos_opened_configs (0 or 1), frozen_oos_strategy_id and protocol id.

This does not solve all model-selection bias. The validation set still selects among many
configurations; OOS is retained as the untouched final test for one frozen winner.
Repeated reruns after reading OOS would contaminate it again and require a new holdout.

No order path or trading authority is added.
research_only=true; can_trade=false; capital_permission=DENY.
