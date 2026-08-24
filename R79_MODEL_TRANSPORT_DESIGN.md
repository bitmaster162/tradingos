# TradingOS R79 — Model Transport Boundary R1

## Objective

R79 introduces the transport boundary between the already-canonical R78 bounded AI
Analyst contract and a future model/provider implementation.

This slice deliberately separates **capability** from **authority**:

`Decision Brief v2 → R78 request/prompt → R79 transport envelope → injected adapter → R78 post-model validator → human`

R79 R1 does **not** contain a live provider client. It defines and tests the exact
transport contract, request/response binding, adapter-mode restrictions, replay binding,
and post-model validation path before any live network capability is admitted.

## Canonical dependency

R79 R1 is qualified against canonical TradingOS commit:

`039cce9dad58805b147f09f53a76a9c5616e61c9`

R78 public contract:

`tools/tradingos_ai_analyst_contract.py`

R79 requires the R78 request, prompt, policy, source brief, and response validator.
It never substitutes its own market facts or semantic acceptance rules.

## Transport modes

R79 R1 allows exactly one adapter mode:

`MOCK_LOCAL`

The policy hard-denies:
- HTTP/network provider calls;
- API keys, bearer tokens, credentials, or environment-secret reads;
- streaming;
- provider tool/function calls;
- provider-side browsing or external sources;
- retries;
- asynchronous/background dispatch;
- automatic execution of any returned content.

A future live-provider adapter must be a separate source slice with its own threat
model, exact provider destination allowlist, credential policy, tests, and authority gate.

## Envelope binding

The transport envelope binds:
- exact R78 request SHA-256;
- exact deterministic prompt SHA-256;
- exact transport policy SHA-256;
- R78 request ID and Decision Brief SHA-256;
- provider/model labels;
- adapter mode;
- hard safety ceiling.

The envelope ID is derived from those bindings. Mutation or cross-request replay is rejected.

## Response path

The adapter output is never accepted directly. R79 passes the returned object through
canonical R78 `validate_response()` against the original request, R78 policy, and source
Decision Brief.

Only after that validation passes does R79 emit a transport receipt.

## Authority ceiling

- `execution_authority=NONE`
- `signals_allowed=false`
- `orders_allowed=false`
- `can_trade=false`
- `capital_permission=DENY`
- `confers_authority=false`
- `network_call_authorized=false`

No deployment, runtime mutation, provider call, external send, order, wallet, trading,
or capital effect is performed by this candidate.
