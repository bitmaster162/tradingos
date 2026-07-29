# TradingOS R62 BTC crowding-exhaustion challenge

Terminal: `INSUFFICIENT_DATA`

This was one preregistered research hypothesis. It did not modify TradingOS,
emit a signal or order, or affect capital.

## Causal spine

1. The completed R59 result was confirmed published through Return Broker.
2. R62 started from accepted R59 HEAD
   `7e9351003da24281b1748c5e018a84068aa4d517` in a disposable branch.
3. The single hypothesis, features, thresholds, clocks, horizons, costs,
   controls, chronological split, bootstrap, and disposition gates were
   committed before source retrieval.
4. The initial monthly metrics path returned HTTP 404 before a source manifest
   or evaluator result existed.
5. A source-layout-only correction to official daily metrics archives was
   committed at `4f7cfd5c734e15eec32282bc9d8727df23ff3bf1`.
   Analytical parameters did not change.
6. The corrected freeze preceded retrieval of 421 public files.
7. A parser/path implementation defect was documented and repaired before any
   threshold or result was emitted. Analytical parameters did not change.
8. The evaluator ran twice; all six generated result files were identical by
   SHA-256.
9. Fourteen offline contract tests passed.
10. Active integrity remained clean: 1,078 files, zero drift,
    `can_trade=false`.

## Frozen result

- OOS candidate feature bars: 4,340
- Complete feature bars: 4,340
- Feature coverage: 100 percent
- Primary raw signals: 9
- Primary non-overlapping and matched signals: 6
- Neighbor sensitivity raw signals: 14
- Neighbor sensitivity matched signals: 10
- Primary signals in first chronological half: 6
- Primary signals in second chronological half: 0

The primary sample is below the frozen minimum of 30, so the required
disposition is `INSUFFICIENT_DATA`.

## Observed sign, not a classified conclusion

Primary +1h:

- Mean matched underperformance after cost: -0.0015758272905234075
- Median: -0.001449711376653261
- Bootstrap 95 percent lower mean: -0.006328086956732361
- Matched win rate: 0.3333333333333333

Primary +4h:

- Mean matched underperformance after cost: -0.0010508820233132144
- Median: -0.0002796893724181337
- Bootstrap 95 percent lower mean: -0.004091940845101989
- Matched win rate: 0.5

Neighbor sensitivity +4h mean was also negative:
`-0.0007306666708977161`.

These observations lean against the short crowding-exhaustion mechanism, but
the preregistered sample floor forbids upgrading them to a classified KILL.
The result is not evidence of a tradable edge.

## Strict continuation rule

No retune, looser-band promotion, backfill, or implementation integration is
allowed from this result. A future append-only watch would need at least 24
additional primary matched observations under the identical frozen contract
before classification.

`can_trade=false`

`capital_permission=DENY`
