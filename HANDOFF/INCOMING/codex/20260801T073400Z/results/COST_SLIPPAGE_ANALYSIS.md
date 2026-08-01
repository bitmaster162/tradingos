# M1 cost and slippage analysis

The frozen round-trip ledger is `12 bps`: `5 bps` entry fee, `5 bps` exit fee,
`1 bp` entry slippage, and `1 bp` exit slippage. It is deducted exactly once
from each complete observation.

| Hypothesis | N | Primary gross mean before 12 bps | Primary net mean | Cost changes mean sign |
|---|---:|---:|---:|---|
| M1_H01_PRESSURE_OI_ABSORPTION | 1 | -0.0060% | -0.1260% | No |
| M1_H02_BTC_SFP_ETH_SMT_TRIGGER | 41 | -0.0334% | -0.1534% | No |
| M1_H03_REGIME_HIDDEN_RSI_CONTINUATION | 10 | -0.6617% | -0.7817% | No |

H01's one-hour secondary matched edge is positive after cost, but the frozen
primary four-hour edge is negative and only one matched independent observation
exists. This cannot support promotion.

Costs are conservative research assumptions, not a venue fee claim. No result
authorizes execution or parameter retuning.
