# P0 TORTURE / REPLAY R6 — Asymmetric Human Custody / Nonce Epoch

Status: DRAFT CANDIDATE / SHADOW ONLY / NO EFFECT

R6 consumes the Control Center asymmetric approval verification and binds it back to the exact R4 human reveal and domain-history closure.

## New closure

`bitevo.shadow_asymmetric_reveal_closure.v1`

Required chain:

```text
R4 human reveal
  -> R4 subject manifest
  -> R4 domain-history closure
  -> Control Center asymmetric approval verification
  -> exact externally expected approval digest
  -> exact externally expected credential-registry digest
  -> exact externally expected cumulative nonce-registry digest
  -> R6 asymmetric reveal closure
```

## TradingOS independent checks

TradingOS independently requires the same case/packet/Twin/reveal choice/time, human/custody policy, credential/public-key digests, algorithm/key epoch, verifier policy, origin/RP id, external asymmetric-verifier assertion, authenticator user-present/user-verified flags, credential/epoch guards, nonce/challenge unused guards and no-write next credential/nonce registry candidates. The reveal-intent digest is independently recomputed.

## Adversarial matrix

R6 attacks reveal-choice transplant, old key epoch, public-key transplant, wrong origin/RP, substituted nonce-registry digest, locally rehashed approval replacing an independently retained digest, local signature-verification overclaim, physical-presence overclaim, missing nonce/challenge guard and effect smuggling through next registry candidates.

Control Center separately attacks nonce reuse, used challenge ids, revoked credentials, sign-counter rollback/reuse, epoch mismatch, missing authenticator user verification and assertion effect smuggling.

## Evidence ceiling

R6 does not claim local asymmetric signature mathematics. The trusted external verifier remains part of the trust root. A future production path may replace that assertion with independently verified WebAuthn/public-key proof without changing the surrounding case/nonce/key-epoch contracts.

Even valid asymmetric custody remains evidence only:

`authenticated reveal != current truth != execution approval != trade permission`.

Fresh exact R6 code/workflow head `0bbdf1c8c7dbdca2bd16c11ec173247f9112c808` produced P0 Shadow Verify run `32301131751`, which completed before executable steps were exposed (`steps=null`, no job logs). Classification: `CI_BLOCKED_PRE_JOB / NOT_A_CODE_TEST_FAILURE`. No R6 TradingOS CI PASS is claimed and no manual rerun was requested.

Fixed ceiling: `merge=false`, `deploy=false`, `runtime=false`, `human_gate_write=false`, `credential_registry_write=false`, `nonce_registry_write=false`, `current_truth_apply=false`, `executor=false`, `signal=false`, `order=false`, `can_trade=false`, `capital_permission=DENY`.
