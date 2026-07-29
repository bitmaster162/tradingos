# R52 Arb Radar forward-edge audit proposal

This proposal is offline/read-only and fail-closed. It does not alter Active,
run the Arb service, access credentials, or send orders.

Run:

```powershell
python arb_radar_r52_audit.py `
  --snapshot <arb.json> `
  --engine <arb_engine.py> `
  --service <arb_service.py> `
  --out <output-directory> `
  --captured-at 2026-07-29T13:25:48Z

python -m unittest -v test_arb_radar_r52_audit.py
```

Current evidence is one timestamp-locked snapshot, so the only permitted
terminal is `INSUFFICIENT_FORWARD_EVIDENCE`.
