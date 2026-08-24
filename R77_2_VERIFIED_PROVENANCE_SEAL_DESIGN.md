# TradingOS R77.2 — Verified Provenance Seal R1

## Blocking finding repaired

R77.1 placed the full upstream hash chain inside the Decision Brief input snapshot,
but it trusted the `input_binding` carried by the supplied R77 result.

Adversarial review proved that a caller could mutate `result.input_binding` while
leaving the R77 snapshot unchanged; R77.1 accepted and sealed the false provenance claim.

## R77.2 verification rule

R77.2 does not trust the supplied R77 result.

Inputs:
1. raw Watchtower capture;
2. Watchtower report;
3. Market Radar report;
4. supplied R77 Market Decision Bridge result.

Before sealing, R77.2 runs:

`canonical = tradingos_market_decision_bridge.build_bridge(capture, watchtower, radar)`

and requires:

`supplied_result == canonical`

Only the reconstructed canonical result can be sealed.

Therefore:
- a forged `input_binding` fails;
- a forged snapshot with a recomputed snapshot hash fails;
- a forged attention context fails;
- stale/mismatched capture, Watchtower, or Radar inputs fail.

## Sealed provenance

The Decision Brief input snapshot includes:
- exact seal producer SHA-256;
- explicit `verified=true` reconstruction method;
- exact R77 bridge producer SHA-256;
- exact verified R77 result SHA-256;
- exact pre-seal snapshot SHA-256;
- full reconstructed Watchtower/Radar/Liquidity binding;
- original per-source observation rows.

Market semantics are unchanged; only provenance is replaced.

## Authority ceiling

- execution_authority = NONE
- signals_allowed = false
- orders_allowed = false
- can_trade = false
- capital_permission = DENY
- confers_authority = false

No network. No credentials. No AI. No deployment/runtime/trading effect.
