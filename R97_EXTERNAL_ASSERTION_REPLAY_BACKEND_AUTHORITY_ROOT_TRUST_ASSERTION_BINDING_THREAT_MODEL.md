# TradingOS R97 — Backend Authority-Root Trust Assertion Binding Threat Model R1

## Trust boundaries

1. Exact fully validated R96 binding and complete upstream chain.
2. Exact R97 trust-assertion policy.
3. One exact externally retained backend-authority-root trust assertion.
4. Independently supplied SHA-256 of the complete assertion.
5. Human/archive consumer.

## Threats and controls

| Threat | R97 control |
| --- | --- |
| Tampered R96 admitted | full canonical R96 validation |
| Trust assertion substituted | complete assertion must match independently supplied SHA-256 |
| Assertion bound to another chain | exact deterministic R97 challenge digest |
| Different root/registries/backend metadata transplanted | exact inherited values required |
| Algorithm drift | exact inherited `ED25519` only |
| Hidden trust claims | exact assertion key set |
| Local evaluator silently introduced | `local_trust_evaluation_performed=false` |
| External claim promoted to verified root | inherited `backend_trust_root_verified=false` |
| Evaluator metadata treated as identity/trust proof | evaluator identity/trust outputs false |
| Trust assertion treated as fresh | no nonce/timestamp; freshness false |
| Root trust claim treated as authenticity/key possession | inherited authenticity/key-possession outputs false |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R97 cannot establish truthfulness of the external trust assertion, evaluator identity/key possession/trust, certification or revocation state, backend-root compromise status, backend identity/authenticity, cryptographic artifact identity, independently derived committed-state equality, durability, current global state, or writer exclusion. A future layer that promotes root trust requires separately governed ground-truth evidence.
