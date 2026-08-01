# Known limitations

1. JSON schemas are portable documentation; semantic enforcement is performed
   by the stdlib validators because no external `jsonschema` dependency is used.
2. The receipt signature is a domain-separated SHA-256 integrity binding, not
   asymmetric proof of controller identity.
3. Source packet hashes are validated for form and catalog binding; raw packet
   bytes are intentionally absent from M2A and cannot be read back here.
4. Freshness is a frozen catalog assertion (`FROZEN_COMPLETE`, `STALE`, or
   `UNKNOWN`), not a wall-clock network probe.
5. Bootstrap and permutation helpers are deterministic primitives. A successor
   must freeze block size, repetitions, seeds, estimand, and stopping rule.
6. The terminal engine accepts structured evidence and does not itself prove
   that a future market evaluator generated that evidence honestly.
7. No real market result, profitable strategy claim, paper runtime, or trading
   permission is produced by this package.
