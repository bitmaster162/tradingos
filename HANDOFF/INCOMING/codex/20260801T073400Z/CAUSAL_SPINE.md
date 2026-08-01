# CODEX-02 M1 causal spine

1. `CURRENT_RETURN_REGISTRY.json` was read first. It was stale and had no M1
   entry, so the exact known R62 broker notice was used without a global scan.
2. The accepted R62 ZIP SHA, proposal bundle SHA, branch, HEAD, and tree were
   bound in `BASELINE_RECEIPT.json`.
3. The R62 proposal bundle was cloned into the disposable root and branch
   `codex02/m1-edge-research-marathon`.
4. Exactly three hypotheses, their data periods, costs, metrics, and
   KEEP/KILL/INSUFFICIENT rules were frozen before OOS retrieval in commit
   `d8d560020e91a0c082e11e1f227ee5be42caedb0`.
5. Exactly 307 public Binance Vision archives were downloaded. Their SHA-256
   identities were frozen before evaluation in commit
   `a30049a286bdf7023eadd80a657a0bcaa8914507`.
6. The evaluator ran once on the frozen July 2026 OOS set. A second run with
   identical inputs produced byte-identical outputs. No parameter changed.
7. Results were frozen in commit
   `bf6559ba2306a058593a7ed34515fbc9aa03862b`.
8. A read-only integrity check found all `1,078/1,078` TradingOS Active files
   unchanged with `drift_count=0`.

Final dispositions:

- `M1_H01_PRESSURE_OI_ABSORPTION`: `INSUFFICIENT_DATA` (`1` matched observation).
- `M1_H02_BTC_SFP_ETH_SMT_TRIGGER`: `KILL` (`41` observations; negative net
  primary mean, median, lower bootstrap bound, and both chronological halves).
- `M1_H03_REGIME_HIDDEN_RSI_CONTINUATION`: `KILL` (`10` observations; negative
  net primary and secondary evidence).

No hypothesis qualifies for runtime implementation or transfer to CODEX-05.
This is a completed research/falsification cycle, not trading authority.
