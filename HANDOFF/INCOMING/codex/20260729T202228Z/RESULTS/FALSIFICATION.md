# R62 BTC crowding-exhaustion falsification

**Disposition:** `INSUFFICIENT_DATA`

Reason: fewer than 30 matched primary OOS signals.

- Primary matched OOS signals: 6
- Neighbor sensitivity matched signals: 10
- OOS feature coverage: 1.0
- Primary +1h: `{"bootstrap_95_lower_mean": -0.006328086956732361, "matched_win_rate": 0.3333333333333333, "mean_control_return": 0.00014446750062342042, "mean_matched_underperformance_after_cost": -0.0015758272905234075, "mean_short_net_return": -0.0017202947911468282, "mean_signal_return": 0.0005202947911468284, "median_matched_underperformance_after_cost": -0.001449711376653261, "n": 6}`
- Primary +4h: `{"bootstrap_95_lower_mean": -0.004091940845101989, "matched_win_rate": 0.5, "mean_control_return": 0.00034061676947803784, "mean_matched_underperformance_after_cost": -0.0010508820233132144, "mean_short_net_return": -0.0013914987927912518, "mean_signal_return": 0.00019149879279125223, "median_matched_underperformance_after_cost": -0.0002796893724181337, "n": 6}`
- Primary chronological halves +4h: `{"first": {"1h": {"bootstrap_95_lower_mean": -0.006328086956732361, "matched_win_rate": 0.3333333333333333, "mean_control_return": 0.00014446750062342042, "mean_matched_underperformance_after_cost": -0.0015758272905234075, "mean_short_net_return": -0.0017202947911468282, "mean_signal_return": 0.0005202947911468284, "median_matched_underperformance_after_cost": -0.001449711376653261, "n": 6}, "4h": {"bootstrap_95_lower_mean": -0.004091940845101989, "matched_win_rate": 0.5, "mean_control_return": 0.00034061676947803784, "mean_matched_underperformance_after_cost": -0.0010508820233132144, "mean_short_net_return": -0.0013914987927912518, "mean_signal_return": 0.00019149879279125223, "median_matched_underperformance_after_cost": -0.0002796893724181337, "n": 6}}, "second": {"1h": {"bootstrap_95_lower_mean": null, "matched_win_rate": null, "mean_control_return": null, "mean_matched_underperformance_after_cost": null, "mean_short_net_return": null, "mean_signal_return": null, "median_matched_underperformance_after_cost": null, "n": 0}, "4h": {"bootstrap_95_lower_mean": null, "matched_win_rate": null, "mean_control_return": null, "mean_matched_underperformance_after_cost": null, "mean_short_net_return": null, "mean_signal_return": null, "median_matched_underperformance_after_cost": null, "n": 0}}}`
- Neighbor sensitivity +4h: `{"bootstrap_95_lower_mean": -0.002792957963095185, "matched_win_rate": 0.5, "mean_control_return": 0.0008655461227472627, "mean_matched_underperformance_after_cost": -0.0007306666708977161, "mean_short_net_return": -0.0015962127936449786, "mean_signal_return": 0.00039621279364497884, "median_matched_underperformance_after_cost": -8.650228758570917e-05, "n": 10}`

The neighboring band was not selected as a replacement. Costs were deducted once. Entry and exits use distinct bar-open snapshots.

This result is research-only and cannot authorize execution.

`can_trade=false`

`capital_permission=DENY`
