# R101 full configured basket coverage

Status: isolated candidate stacked on R100. No runtime deployment.

Base: 4d99edaed96cf5cfe7783c2cd00f96bee452bb45

## Finding

Relative-strength features are aligned by exact timestamp, which is correct. But signal generation
previously dropped unavailable configured alt returns and averaged whatever remained.

A configuration identified as ETH+SOL+BCH could therefore produce a signal using only ETH (or any
partial subset) when another constituent was missing at the current or lookback timestamp. The
strategy identity did not record that shrinkage.

This can be material around listing starts, missing bars or incomplete cache coverage.

## Candidate

- Every configured alt constituent must have both the current and lookback return available.
- Missing any constituent skips the signal; the basket never silently shrinks.
- Signals record expected and observed coverage counts plus constituent symbols.
- Signal horizon eligibility is aligned with R100 exact max_hold_bars semantics.

This does not prove the chosen basket was point-in-time investable or free from survivorship bias.
A separate universe-provenance policy is still required for broad cross-sectional research.

research_only=true; can_trade=false; capital_permission=DENY.
