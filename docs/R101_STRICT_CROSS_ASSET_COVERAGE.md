# R101 strict cross-asset coverage

Status: isolated research candidate stacked on R99. No runtime deployment.

## Finding

Relative-strength configs declare an exact alt basket in strategy_id, for example
ETH+SOL+BCH. The previous signal generator silently dropped an alt return when
that symbol had no aligned current/lookback value, then computed the basket and
confirmation threshold on the remaining subset.

That changes the effective strategy through time while preserving the same id.
It is especially relevant around later listings or missing cross-asset bars.
The inspected Phase-A cache itself shows SOL history starting later than the
BTC/ETH/BCH histories.

## Candidate rule

For every configured alt symbol:
- the aligned current bar must exist;
- the aligned lookback bar must exist;
- the return must be finite/available.

If any configured alt return is unavailable, the signal is not emitted.
No neighboring timestamp, reduced basket or majority denominator is substituted.

This removes dynamic-basket drift. It does not remove the broader selection bias
from choosing today's universe with knowledge of later survivors. That requires
a predeclared prospective universe or point-in-time membership data.

research_only=true; can_trade=false; capital_permission=DENY.
