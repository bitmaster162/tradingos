# Arbiter CTI Checklist

## Role

- `CTI` is a rotation overlay, not a direct entry trigger
- use it on `H4 / D1`
- `H1` is early warning only

## Core blocks

- `ETH/BTC`: is `EMA50` sloping up or down on `H4` and `D1`?
- `BTC.D`: is dominance rising or falling relative to `EMA50`?
- stables: is `USDT/USDC` netflow positive with `z-score > +0.5`?

Rule:

- at least `2 of 3` core blocks must agree

## Secondary blocks

- `OI mix`: does BTC / ETH / alt OI support the rotation?
- funding: is there compression to `0` after crowding?

## Thresholds

- `CTI >= 65` -> `ALT MODE`
- `35 < CTI < 65` -> `NEUTRAL`
- `CTI <= 35` -> `BTC / DEFENSIVE`

Confirmation:

- hold threshold for `2` candles on main TF
- confirm on both `H4` and `D1`

## Actions

### ALT MODE

- rotate toward `ETH` and strong alts in steps
- keep `BTC` as hold / hedge layer
- do not full-send on first signal

### NEUTRAL

- reduce rotation aggression
- work level-to-level
- keep exposure balanced

### BTC / DEFENSIVE

- reduce alts
- prefer `BTC`, `cash` or `stables`
- hedge alt basket if needed

## Risk

- base rotation risk `<= 1%`
- hard cap `<= 2%`
- staged adds only
- near `35 / 65` boundaries use hedge overlay or smaller size

## Invalidators

- `ETH/BTC` slope reversal
- `BTC.D` break against the regime
- negative stable inflow shock
- OI mix deterioration
- funding recrowding

## Hard no-rotate

- no `H4 + D1` confirmation
- only one core block agrees
- CTI crosses threshold for one candle and immediately fades
- no stable inflow support and OI mix is weak
- local execution setup contradicts the regime and there is no hedge plan
