# Threat model

## Protected truths

- Exact M1 identities and terminals cannot be renamed, rerun, or promoted.
- Final-test rules, time intervals, costs, and multiple-testing family are
  frozen before outcomes.
- Source identity is bound by full SHA-256 and immutable references.
- Readiness is derived again at authorization time, not trusted as an assertion.
- `KEEP_FOR_FORWARD_PAPER` is measurement authorization only.

## Adversaries and failures covered

- renamed killed hypothesis family
- incomplete or mutable preregistration
- threshold selection after final-test inspection
- OOS overlap and inadequate purge/embargo
- missing costs and optimistic execution assumptions
- catalog mutation, partial hashes, ambiguous clocks, stale sources
- duplicate rows/events and inadequate join coverage
- one-source or one-event dominance
- placebo performance matching the claim
- forged readiness/controller state
- nondeterministic replay

## Out of scope

Cryptographic signing with a private controller key, vendor authenticity,
market-data acquisition, real outcome computation, strategy implementation,
paper/live execution, and production deployment are deliberately absent.
