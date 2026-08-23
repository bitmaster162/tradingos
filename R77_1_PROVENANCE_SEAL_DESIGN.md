# TradingOS R77.1 — Additive Provenance Seal R1

## Reason

The first R77.1 amendment attempted to rewrite the existing large R77 source file.
Provider object creation stopped because the transferred source blob SHA did not match
the sealed candidate SHA. No tree, commit, or branch ref changed.

This revision is additive only.

## Pipeline

`R77 Market Decision Bridge result → R77.1 Provenance Seal → Decision Brief v2`

The seal puts into the exact Decision Brief input snapshot:
- seal producer path and SHA-256;
- R77 bridge producer path and SHA-256;
- pre-seal R77 snapshot SHA-256;
- full R77 result SHA-256;
- full Watchtower / Radar / Liquidity upstream binding;
- unchanged source observation rows.

Validation rejects any market-semantic mutation: only provenance may change.

## Authority

- execution_authority = NONE
- signals_allowed = false
- orders_allowed = false
- can_trade = false
- capital_permission = DENY
- confers_authority = false

No network, credentials, AI, deployment, runtime mutation, or trading effect.
