# TradingOS R100 — Threat Model

| Threat | R100 control |
| --- | --- |
| R99 bypassed | full exact R99 validator consumed first |
| Self-authored provenance digest promoted to independence | expected provenance digest is bound, but independence remains false |
| Provenance record substituted | complete stable SHA-256 must equal supplied expected digest |
| R99 binding transplanted | exact R99 ID and complete R99 SHA-256 lineage required |
| Derivation/equality digest transplant | exact R99 digest lineage required |
| Source artifact transplant | exact committed/readback source digests required |
| Projection or canonicalization drift | exact IDs and versions required |
| Derivation tool drift | exact tool SHA-256 required |
| Unsupported recomputation method | one explicit allowlisted method |
| Missing recomputation assertion | all bounded recomputation claim booleans required true |
| Hidden credential/raw field | exact record key set rejects extras |
| External record treated as trusted verifier evidence | verifier identity/trust remain false |
| External record treated as durable retention proof | retention remains false |
| Equality provenance treated as provider honesty | provider honesty remains false |
| Evidence treated as durable/current state | inherited durable/current-state ceiling remains false |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risk

R100 still does not establish independent provenance for the expected provenance-record digest, verifier identity, verifier trust root, durable independent retention, provider honesty, backend durability, or global current state. Any later layer that promotes one of those properties requires a separately retained and separately governed evidence source.
