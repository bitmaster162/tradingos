# Drift and limitations

1. The dispatch summary described the refined RANGE setup as an observer
   candidate. Current source evidence additionally contains a later untouched
   calendar-OOS rejection and an immutable tombstone. The no-resurrection rule
   takes precedence; the track is `KILL`.
2. The original HYP-SPOT-LEAD report used an immutable snapshot ending
   `2026-06-23T23:00:00+07:00`. A later whole-history report changed the rejected
   best-slice metrics, so it was not used as the frozen baseline. Only bars
   strictly after the original cutoff were evaluated with the original config.
3. The continuous liquidation score has 153 unique post-lock context rows but
   only one qualifying edge signal/outcome. Context-row volume is not outcome
   sample size.
4. The source package containing candidate reports is non-Git. Its files are
   bound individually by SHA-256; executable code comes from the exact M2A Git
   bundle (`HEAD 31a095e...`, tree `f9c60da...`).
5. Broad root unittest discovery has two baseline import errors because the
   Bitunix tests reference missing docs. Targeted M2B, relevant research and M2A
   suites pass. This task does not patch unrelated baseline drift.
6. No new market data was downloaded. The latest copied local futures bar is
   `2026-07-26T13:00:00+07:00`; evidence after that timestamp is unknown.

