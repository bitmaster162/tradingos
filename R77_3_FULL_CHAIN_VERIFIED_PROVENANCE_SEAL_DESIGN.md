# TradingOS R77.3 — Full-Chain Verified Provenance Seal R1

## Purpose

R77.2 closed the forged `R77.input_binding` attack by reconstructing R77 from its
immediate inputs. Post-write review then found that the immediate inputs themselves
still contained two claim-bound segments:

1. Market Radar carried Liquidity report/capture hashes without R77.2 receiving the
   actual Liquidity artifacts.
2. Watchtower was checked for capture-hash binding but was not itself reconstructed
   from the raw Watchtower capture.

R77.3 closes both boundaries.

## Canonical chain

The verifier pins and imports the exact canonical source modules by Git blob SHA-1:

- Watchtower `96f00327e5bd8a77612d7b26718d4c9951f2be73`
- Liquidity Lens `193ac1c869dd479dac47c35cede777cc34bce687`
- Market Radar `3e4df1d56648483254667b39d16b1879434ca858`
- R77 Bridge `e6e0f6ecad22068acd82ca0588ad2dfb5fdd89b4`

It then reconstructs, in order:

`watchtower = build_watchtower(watchtower_capture)`

`liquidity = build_lens(liquidity_capture)`

`radar = build_radar(watchtower, liquidity)`

`r77 = build_bridge(watchtower_capture, watchtower, radar)`

Every supplied derived artifact must be exactly equal to its deterministic reconstruction.

## Sealed bytes

The downstream Decision Brief snapshot bytes bind:
- canonical source blob identities;
- raw Watchtower capture hash;
- reconstructed Watchtower report hash;
- raw Liquidity capture hash;
- reconstructed Liquidity report hash;
- reconstructed Radar hash;
- reconstructed R77 result hash;
- pre-seal R77 snapshot hash;
- exact R77 input binding and source-observation rows.

Only provenance is replaced. Market semantics must remain byte-for-byte equivalent
after restoring the original provenance object.

## Fail-closed adversarial boundary

Rejected:
- forged Watchtower report;
- stale/mutated Watchtower capture;
- forged Liquidity report;
- mutated Liquidity capture;
- forged Radar Liquidity report/capture hashes;
- mutated Radar Liquidity context;
- mutated Radar report;
- forged R77 result/input binding;
- sealed provenance mutation;
- sealed market-semantic mutation;
- canonical source blob drift.

## Authority ceiling

- execution_authority = NONE
- signals_allowed = false
- orders_allowed = false
- can_trade = false
- capital_permission = DENY
- confers_authority = false

No network, credentials, AI transport, workflow rerun, deployment, runtime mutation,
external send, order, wallet, trading, or capital effect.
