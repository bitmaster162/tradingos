# TradingOS R99 — Committed-State / Readback Equality Binding Threat Model R1

## Trust boundaries

1. Exact R98 binding plus its full upstream validation inputs.
2. Exact R99 policy.
3. Independently retained committed-state derivation record and expected SHA-256.
4. Independently retained readback-state derivation record and expected SHA-256.
5. Independently retained equality record and expected SHA-256.

## Threats and controls

| Threat | R99 control |
| --- | --- |
| Synthetic R90/R91/R98 fixture promoted to provider truth | deny known placeholder digests and fixture commit id |
| Commit receipt digest substituted for committed-state digest | exact inequality check |
| Readback source transplanted | must equal inherited R98 readback-state digest |
| Projection mismatch | exact projection schema id/version equality |
| Canonicalization mismatch | exact canonicalization id/version equality |
| Derivation tool drift | exact derivation-tool SHA equality |
| Boolean `read_after_write_match` treated as proof | booleans are not accepted as equality evidence |
| Record rewritten after expected digest supplied | complete stable SHA-256 must match independently supplied expected digest |
| Equality record transplanted to another R98 chain | exact R98 binding id/SHA and inherited receipt/readback digests required |
| Unequal canonical projected states | exact SHA-256 equality required |
| Equality treated as durability/current truth | durability/current-state outputs remain false |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R99 cannot prove that an external provider is honest merely because records are internally consistent. It does not authenticate backend operators, prove durable commit, prove current global state, or confer execution authority. Provider material must still be independently acquired and provenance-reviewed.
