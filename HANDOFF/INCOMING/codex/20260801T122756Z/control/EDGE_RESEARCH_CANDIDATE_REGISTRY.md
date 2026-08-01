# Trading Edge Candidate Registry R1

Status: `PRE-OUTCOME / CONTROLLER-OWNED`

This registry is a set of candidate research families. It is not an edge claim,
not a recommendation, and not authorization to run all families.

No family becomes eligible until the Antigravity data-readiness census returns
an exact status and provenance record.

## Immutable exclusions

The following M1 families are closed:

- `M1_H02_BTC_SFP_ETH_SMT_TRIGGER` — `KILL`
- `M1_H03_REGIME_HIDDEN_RSI_CONTINUATION` — `KILL`

They may not be renamed, lightly reparameterized, recombined, or described as a
new family unless a controller review proves a materially different causal and
data contract.

## Candidate E1 — Pressure/OI Absorption Extension

Purpose: continue only the unresolved M1-H1 family with new independent events.

Required channels:

- trade/aggTrade or equivalent aggressor-side flow;
- mark/index price;
- open interest;
- timestamp semantics and join coverage;
- optional order-book/depth for confirmation.

Hard distinction from M1:

- no reuse of the single M1 event as new evidence;
- no threshold tuning on the final test period;
- event clustering and independence must be explicit;
- costs and delayed-entry sensitivity are mandatory.

## Candidate E2 — OI Flush + Spot-Led Reclaim

Claim class: after a forced OI/liquidation flush, a reclaim led by spot rather
than perp flow may have different forward behavior.

Required channels:

- OI;
- liquidations or defensible liquidation proxy;
- spot and perp trades;
- price/mark/index;
- cross-market clock alignment.

Primary falsifiers:

- reclaim is actually perp-led;
- result disappears after delayed entry;
- effect is one-event or one-regime dominated;
- net expectancy is non-positive after costs.

## Candidate E3 — Funding/Basis Crowd Unwind

Claim class: extreme crowding combined with price/OI dislocation may precede a
bounded unwind.

Required channels:

- funding publication and effective timestamps;
- basis or spot/perp prices;
- OI;
- trade flow;
- exact funding-cycle semantics.

Primary falsifiers:

- publication-time leakage;
- effect exists only before realistic entry;
- result disappears after funding, spread and slippage costs;
- exchange-specific artifact.

## Candidate E4 — Liquidation Cascade Absorption/Reversal

Claim class: a liquidation cascade that fails to extend and is absorbed by
opposing flow may differ from a continuation cascade.

Required channels:

- liquidation events or high-integrity proxy;
- trade flow/CVD;
- price;
- OI;
- optional depth/book replenishment.

Primary falsifiers:

- proxy does not distinguish liquidation from ordinary volatility;
- absorption definition uses future information;
- cluster dedupe collapses sample;
- tail losses dominate mean expectancy.

## Candidate E5 — Cross-Venue Lead/Lag After Latency and Costs

Claim class: one venue or market layer may lead another over a horizon longer
than realistic ingestion/execution latency.

Required channels:

- at least two independently timestamped venues or market layers;
- measured or bounded clock skew;
- fees, spread and latency model;
- stable symbol/contract mapping.

Primary falsifiers:

- apparent lead is clock skew;
- edge horizon is shorter than total latency;
- venue outages or sparse data dominate;
- result vanishes after realistic costs.

## Candidate E6 — Volatility Compression + Order-Flow Confirmation

Claim class: breakout quality may differ when pre-breakout compression is
followed by explicit spot/perp order-flow confirmation.

Required channels:

- price/volatility series;
- spot/perp trade flow;
- optional OI/depth;
- deterministic breakout and confirmation definitions.

Primary falsifiers:

- threshold mining;
- direction-randomized placebo performs similarly;
- no benefit over compression-only baseline;
- post-cost out-of-sample result is non-positive.

## Selection rule after census

Use a lexicographic gate, not a weighted score:

1. provenance and timestamp semantics are acceptable;
2. hypothesis is not a duplicate or renamed killed family;
3. required channels have sufficient independent coverage;
4. costs and latency can be modeled;
5. the claim is falsifiable with a frozen final test;
6. compute/storage cost fits the round budget.

At most three families enter one CODEX-02 cycle.
