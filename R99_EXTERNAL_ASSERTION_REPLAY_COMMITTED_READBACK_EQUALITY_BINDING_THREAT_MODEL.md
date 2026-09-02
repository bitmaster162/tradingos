# TradingOS R99 R2 — Threat Model

## Controls

| Threat | R2 control |
| --- | --- |
| Self-computed expected SHA promoted to independent evidence | no independence promotion; `expected_digest_independence_verified=false` |
| Record changed relative to supplied expected SHA | complete stable SHA must match |
| R98 safety ceiling silently dropped | explicit inherited safety materialization and drift rejection |
| Tampered R98 admitted | full R98 validator is consumed first |
| Synthetic fixture promoted to provider truth | known fixture digests and `commit-r90-0001` rejected |
| Commit receipt substituted for committed-state artifact | exact inequality check |
| Readback source transplanted | exact inherited R98 readback-state digest required |
| Projection/canonicalization/tool drift | exact equality required |
| Unequal projected state | exact SHA equality required |
| Equality treated as durability/current truth | inherited false ceiling retained |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risk

R2 cannot establish expected-digest independence or external provider provenance. That requires a distinct externally retained provenance/verifier layer.
