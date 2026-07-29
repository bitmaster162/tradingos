# R62 source endpoint correction before outcome inspection

The initial source plan used a nonexistent Binance Vision monthly layout for
`metrics`:

`futures/um/monthly/metrics/BTCUSDT/BTCUSDT-metrics-2025-06.zip`

The downloader returned HTTP 404 before creating a source manifest or running
the evaluator.

Failure facts:

- Partial files written: 26
- Partial bytes: 502474
- `SOURCE_MANIFEST.json` created: no
- Evaluator started: no
- Calibration or OOS contents inspected: no

The official source-equivalent daily layout replaces only the archive packaging:

`futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-YYYY-MM-DD.zip`

Unchanged:

- venue and instrument;
- `sum_open_interest`;
- `sum_toptrader_long_short_ratio`;
- metric timestamp semantics;
- warm-up, calibration, and OOS periods;
- all features, thresholds, costs, horizons, controls, bootstrap, and
  disposition gates.

The failed partial directory is retained as evidence and is not used by the
corrected evaluator. Corrected retrieval uses a fresh directory.

This superseding freeze still precedes all outcome inspection.

`can_trade=false`

`capital_permission=DENY`
