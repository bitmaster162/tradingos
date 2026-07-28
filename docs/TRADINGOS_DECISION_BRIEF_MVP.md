# TradingOS VIP Daily Decision Brief

## Product boundary

This is a local, read-only operator product. It converts one BTCUSDT
`market_snapshot.json` into:

- `brief.json` for machines;
- `brief.md` for review and journaling;
- `brief.html` for a readable and printable daily brief.

It never sends an order, uses credentials, changes TradingOS runtime, or grants
trading permission. `WATCH_LONG` and `WATCH_SHORT` mean “watch this
hypothesis”, not “enter a position”. Every artifact keeps `can_trade=false`.

## Frozen sample replay

From the repository root:

```powershell
python tools/tradingos_decision_brief.py `
  --input examples/tradingos_decision_brief/market_snapshot.sample.json `
  --out-dir _dl/decision_brief_sample `
  --now 2026-07-29T00:30:00Z `
  --pilot-log _dl/decision_brief_sample/pilot_log.jsonl `
  --pilot-day DAY_1
```

Open `_dl/decision_brief_sample/brief.html` in a browser or print it to PDF.
The frozen `--now` value is only for reproducible sample replay.

## Real daily use

1. Copy the sample shape to a working file outside the source tree.
2. Replace every value with a fresh observation.
3. Set `as_of` and provenance timestamps in UTC.
4. Keep `can_trade` exactly `false`.
5. Run without `--now`:

```powershell
python tools/tradingos_decision_brief.py `
  --input C:\path\to\market_snapshot.json `
  --out-dir C:\path\to\daily_brief `
  --pilot-log C:\path\to\seven_day_pilot_log.jsonl `
  --pilot-day 2026-07-29
```

Exit code `0` means the input gate passed. Exit code `3` means the tool still
wrote a blocked `NO_ACTION` brief, but stale, missing, conflicting, or unsafe
input failed closed. Exit code `2` means the input could not be parsed.

## Decision logic

The tool creates LONG and SHORT hypotheses independently. It counts evidence
from market structure, EMA alignment, price/OI alignment, spot flow,
derivatives crowding, and relative volume. A watch stance requires:

- support score of at least `3.0`;
- score margin of at least `2.0`;
- at least three independent supporting dimensions.

Anything weaker becomes `NO_ACTION`. Scores are deterministic weights, not
calibrated probabilities and not claims of profitability.

## Required data

The policy requires fresh `ohlcv`, `open_interest`, `funding`, and `spot_flow`
inputs with matching provenance. Snapshot age may not exceed 90 minutes.
Missing sources, explicit conflicts, inconsistent support/resistance,
unsupported symbol/timeframe, or any `can_trade` value other than `false`
blocks the brief.

## Seven-day pilot

Use one line per day. Record the operator’s prior, whether the brief changed a
decision, and whether it prevented an impulsive or unsupported decision.
`examples/tradingos_decision_brief/seven_day_pilot_log.template.jsonl` is a
seven-row blank template. The CLI suppresses duplicate rows with the same
`pilot_day` and `brief_id`.

The pilot evaluates usefulness and discipline, not trading profitability.
