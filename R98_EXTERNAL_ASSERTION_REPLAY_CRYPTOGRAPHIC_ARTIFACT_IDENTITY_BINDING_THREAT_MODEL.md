# TradingOS R98 — Cryptographic Artifact Identity Binding Threat Model R1

## Trust boundaries

1. Exact fully validated R97 binding and complete upstream evidence chain.
2. Exact R98 artifact-identity policy.
3. One exact externally retained cryptographic-artifact identity record.
4. Independently supplied SHA-256 of the complete record.
5. Human/archive consumer.

## Threats and controls

| Threat | R98 control |
| --- | --- |
| Tampered R97 admitted | full canonical R97 validation |
| Artifact identity record substituted | complete record digest must equal independently supplied SHA-256 |
| Record bound to another R97 chain | exact deterministic R98 challenge digest |
| Public key transplanted | exact inherited `public_key_sha256` required |
| Backend/key metadata transplanted | exact inherited backend ID/key ID required |
| Commit signature redirected to another payload | exact target must equal inherited external commit-receipt SHA-256 |
| Readback signature redirected to another payload | exact target must equal inherited readback-evidence SHA-256 |
| Readback state transplanted | exact inherited readback-state SHA-256 required |
| Algorithm drift | exact inherited `ED25519` only |
| Hidden raw artifacts or hidden claims | exact record key set; raw bytes forbidden |
| Local signature verifier silently introduced | local cryptographic verification forbidden |
| Digest identity treated as signature validity | no signature-validity or authenticity promotion |
| Artifact identity treated as backend identity/key possession | inherited identity/key-possession outputs remain false |
| Artifact identity treated as durable/current truth | inherited durability/current-state outputs remain false |
| Trading authority inferred | execution NONE, can_trade false, capital_permission DENY |

## Residual risks

R98 cannot establish that the retained signature digests correspond to valid signatures, that the external verifier used the claimed artifact bytes, that the backend possessed the claimed key, that the backend is authentic, that the authority root claim is true, that the artifacts are fresh/current/durable, or that readback state equals an independently derived committed state. The next independent gap remains committed-state/readback equality.
