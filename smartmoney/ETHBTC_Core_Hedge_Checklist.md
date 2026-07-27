# ETHBTC Core/Hedge Checklist

## Inputs

- pair: `ETHBTC`
- timeframe: `1D`
- history: `>= 220 days`

## Daily checks

- is `Close > SMA200`?
- if yes, how many daily closes in a row?
- what is `HL_30 = High30 / Low30`?
- is price near `SMA200` within `+/- 2%`?
- did price fail below `SMA200` without fast recovery?

## Status

- `CORE` if `Close > SMA200` for `>= 3` closes or `HL_30 >= 1.15`
- `HEDGE` if price is near `SMA200` and `HL_30 < 1.15`
- `RISK-OFF` if price is below `SMA200` without recovery, below by `> 2%`, or `HL_30 <= 1.05`

## Portfolio action

- `CORE` -> ETH can be part of core allocation
- `HEDGE` -> ETH is tactical only, do not add to core
- `RISK-OFF` -> reduce ETH, prefer BTC / cash / stables

## Hard rules

- do not upgrade ETH to core on one random daily poke above `SMA200`
- do not ignore `HL_30`; narrow range means no real impulse
- if dashboard says `HEDGE`, treat ETH longs as tactical, not structural
- if dashboard says `RISK-OFF`, stop arguing with the pair
