# R101 strict context basket identity

Status: isolated research candidate, not deployed.

Finding: relative-strength signal generation previously collected every non-None
configured alt return and averaged only those available values. A configuration
declared as ETH+SOL+BCH could therefore behave as ETH+BCH before SOL history was
available, while retaining the same strategy/config identity.

R101 requires every configured alt symbol to have the requested lookback return
at the signal timestamp. If any configured member is absent or unavailable, no
signal is emitted for that timestamp. Missing archives are not backfilled and the
basket is never silently shrunk.

Scope note: the previously highlighted ETH-only RSR winner is not directly changed
by SOL absence. This correction primarily affects multi-alt configurations and the
fairness of grid comparison among fixed basket definitions.

R101 is stacked on R99 and preserves:
- train/validation grid before single frozen OOS opening;
- time-based folds;
- moving-block bootstrap;
- research-only DENY authority.

It does not make the historical OOS pristine again and does not authorize trading.

research_only=true; can_trade=false; capital_permission=DENY.
