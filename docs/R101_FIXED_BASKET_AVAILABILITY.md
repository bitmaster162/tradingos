# R101 fixed-basket availability

Status: stacked research candidate. No runtime deployment.

Base: R100 full-lifecycle folds at
`b684f4cd12a93adf7d4b5da8137c4a2400b86d61`.

## Finding

Relative-strength configs name a fixed `alt_symbols` basket. The prior signal
generator silently dropped any alt whose current/lookback return was unavailable
and averaged the remaining returns. A config labeled ETH/SOL/BCH could therefore
behave as ETH-only or ETH/BCH during earlier periods.

That is a hidden strategy-definition change through time. It can also obscure
listing/data-availability effects.

## Candidate rule

For a fixed-basket config, every configured alt must have a valid return for the
same current and lookback observations. If any configured member is unavailable,
the strategy emits no signal for that timestamp.

A one-symbol config remains valid when that one symbol is available. Dynamic
membership would require a separate versioned strategy with explicit historical
eligibility rules; it is not inferred here.

This does not make the chosen current asset set survivorship-free. The choice of
ETH/SOL/BCH itself may still reflect ex-post research decisions. Results must be
described as a fixed-hypothesis basket, not as an unbiased historical market
universe.

research_only=true; can_trade=false; capital_permission=DENY.
