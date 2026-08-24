# TradingOS R79 — Model Transport Threat Model R1

## Trust boundaries

1. Deterministic market pipeline / Decision Brief.
2. R78 bounded request and deterministic prompt.
3. R79 transport envelope and adapter selection.
4. Model/provider boundary.
5. Untrusted model response.
6. R78 fail-closed post-model validator.
7. Human operator.

R79 R1 implements boundaries 2–3 and the return path through 6 with a local injected
mock adapter only. Boundary 4 remains disabled.

## Threats and R1 controls

| Threat | R1 control |
| --- | --- |
| Request substitution | Exact R78 request SHA + request_id binding |
| Prompt substitution | Exact prompt SHA and deterministic prompt reconstruction |
| Cross-brief replay | Decision Brief SHA bound into request and envelope |
| Policy substitution | Exact R79 policy SHA bound into envelope |
| Adapter downgrade | Exact `MOCK_LOCAL` mode only |
| Hidden network call | Live modes rejected before adapter invocation; source import scan rejects network/process/provider SDKs |
| Credential exfiltration | Credentials forbidden; no environment or secret access in R79 core |
| Provider tool invocation | Tools/functions/browser/external sources denied by policy |
| Hallucinated market facts | Canonical R78 response validator rejects novel facts/unsupported evidence |
| Hallucinated numbers | Canonical R78 numeric whitelist remains authoritative |
| Probability/signal/order language | Canonical R78 validator remains authoritative |
| Malformed response | Non-object or invalid object rejected fail-closed |
| Response/request mix-up | Response request_id + brief SHA checked by R78 |
| Retry duplication | retries=0 |
| Streaming partial acceptance | streaming=false |
| Async/background dispatch | no async dispatch API in R1 |
| Adapter mutates envelope | adapter receives a deep copy; original envelope hash verified after call |
| Adapter returns side-channel metadata | receipt has exact key set; raw provider metadata not trusted |
| Model/provider drift | provider_id/model_id are labels only in R1; live provider admission is separate |
| Execution authority escalation | hard ceiling remains NONE / DENY / false |

## Deliberately deferred

R79 R1 does not solve live-provider TLS, endpoint pinning, credential injection,
rate limiting, cost caps, live retry semantics, provider response signatures, or
provider-side data retention because no live provider transport exists in this slice.

Those are mandatory gates for a later live-transport amendment.
