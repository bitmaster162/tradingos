# Trading OS Agent Boundary

`C:\Users\coins\TradingOS\Active` is the reviewed canonical runtime.

- Do not edit `tools/`, `tests/`, `ops/`, `configs/`, `portable/`, `scripts/`, `adapters/`, `v7/`, `smartmoney/` or `bitevo/` directly unless the user explicitly assigns you as the reviewing integrator.
- External agents must place proposals under `HANDOFF/INCOMING/<agent>/<timestamp>/` with provenance, a patch or replacement files, tests and claimed evidence.
- A proposal is not runtime code, a strategy registration, a paper signal or trading permission.
- Never add a candidate directly to shared leaderboards or runtime/autostart before preregistration and bounded review.
- Never modify `configs/ACTIVE_SOURCE_INTEGRITY_LOCK.json`. Only the reviewing integrator may reseal it after tests.
- Keep validation, signals, paper entries and orders disabled unless an explicit reviewed gate says otherwise.

Any direct source drift is expected to be quarantined and blocked by `tools/active_source_integrity_guard.py`.
