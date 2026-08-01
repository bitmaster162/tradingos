# Trading Edge Research Marathon M1

Exactly three preregistered hypotheses were evaluated once on the frozen July 2026 OOS set.

## M1_H01_PRESSURE_OI_ABSORPTION

**Disposition:** `INSUFFICIENT_DATA`

Reason: fewer than three independent observations.
Independent observations: 1.
Coverage: 1.000000.
Primary evidence: `{"bootstrap_95_lower_mean": -0.0012601656595511367, "mean": -0.0012601656595511367, "median": -0.0012601656595511367, "n": 1, "sum": -0.0012601656595511367, "win_rate": 0.0}`
Secondary evidence: `{"bootstrap_95_lower_mean": 0.00017359631883124963, "mean": 0.00017359631883124963, "median": 0.00017359631883124963, "n": 1, "sum": 0.00017359631883124963, "win_rate": 1.0}`

## M1_H02_BTC_SFP_ETH_SMT_TRIGGER

**Disposition:** `KILL`

Reason: one or more frozen robustness gates failed.
Independent observations: 41.
Coverage: 1.000000.
Primary evidence: `{"bootstrap_95_lower_mean": -0.00402168448966145, "mean": -0.0015336789987041922, "median": -0.001170264784270979, "n": 41, "sum": -0.06288083894687188, "win_rate": 0.4146341463414634}`
Secondary evidence: `{"bootstrap_95_lower_mean": -0.0029590052778180184, "mean": -0.0014703044352980931, "median": -0.0012, "n": 41, "sum": -0.06028248184722181, "win_rate": 0.2926829268292683}`

## M1_H03_REGIME_HIDDEN_RSI_CONTINUATION

**Disposition:** `KILL`

Reason: one or more frozen robustness gates failed.
Independent observations: 10.
Coverage: 1.000000.
Primary evidence: `{"bootstrap_95_lower_mean": -0.016355244823864003, "mean": -0.007817144403283027, "median": -0.0038017756402496036, "n": 10, "sum": -0.07817144403283027, "win_rate": 0.3}`
Secondary evidence: `{"bootstrap_95_lower_mean": -0.0068328369313704436, "mean": -0.003150682797277525, "median": -0.0025812718609986227, "n": 10, "sum": -0.03150682797277525, "win_rate": 0.2}`

No adaptive parameter change, replacement hypothesis, live execution, or runtime mutation occurred.

`can_trade=false`

`capital_permission=DENY`
