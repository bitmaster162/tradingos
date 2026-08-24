# TradingOS R78 — AI Analyst Contract R1

## Objective

R78 adds an AI reasoning layer **after** deterministic Decision Brief v2 and **before**
the human operator. It does not replace Watchtower, Liquidity Lens, Market Radar,
R77 Market Decision Bridge, or Decision Brief.

Pipeline:

`market capture → deterministic transforms → R77 snapshot → Decision Brief v2 → R78 bounded AI Analyst → human`

## Why a contract layer comes before model transport

A direct LLM call would create an uncontrolled semantic boundary. R78 therefore first
defines a provider-agnostic request/response contract and a post-model validator.

Core R78 has:
- no network client;
- no API key or credential handling;
- no model SDK;
- no tool calling;
- no order/execution path.

A future model-provider adapter must be a separate bounded slice.

## Allowed AI work

The model may:
- explain the deterministic thesis;
- steelman the opposite thesis;
- identify blind spots;
- run a premortem;
- compare existing scenarios;
- restate invalidations;
- ask operator questions.

Every claim/question must cite deterministic evidence IDs created from the Decision Brief.

## Forbidden AI work

The model may not:
- use web/news/memory/outside market knowledge;
- invent market facts;
- introduce numeric literals absent from the Decision Brief;
- assign probabilities;
- create signals;
- give entries/exits/orders/leverage/sizing;
- confer execution authority.

## Post-model guard

The validator fail-closes on:
- unknown or missing evidence references;
- extra response fields;
- new numeric literals;
- URL/external references;
- probability language;
- execution/trading language;
- unsafe permissions;
- request/brief digest mismatch;
- directional thesis on BLOCKED briefs.

Hard ceiling:
- `execution_authority=NONE`
- `signals_allowed=false`
- `orders_allowed=false`
- `can_trade=false`
- `capital_permission=DENY`
- `confers_authority=false`

## R77.1 sealed-input binding update

R78 now accepts Decision Brief input only when the brief provenance reports
`tools/tradingos_market_decision_snapshot_seal.py` as the exact snapshot producer.

The pre-seal R77 bridge producer is rejected. This keeps the AI analyst downstream of
the full provenance seal and prevents accidental fallback to an unsealed snapshot path.

