# P0 TORTURE / REPLAY R6 — Asymmetric Human Custody / Nonce Epoch

Status: DRAFT CANDIDATE / SHADOW ONLY / NO EFFECT

R6 consumes the Control Center asymmetric approval verification and binds it back to the exact R4 human reveal and domain-history closure as `bitevo.shadow_asymmetric_reveal_closure.v1`.

Required chain:

```text
R4 human reveal
  -> R4 subject manifest
  -> R4 domain-history closure
  -> Control Center asymmetric approval verification
  -> independently expected approval digest
  -> independently expected credential-registry digest
  -> independently expected cumulative nonce-registry digest
  -> R6 asymmetric reveal closure
```

TradingOS independently checks same case/packet/Twin/reveal choice/time, subject/provider policy, credential/public-key digests, algorithm/key epoch, verifier policy, origin/RP id, external asymmetric-verifier assertion, authenticator user-present/user-verified flags, credential/epoch guards, nonce/challenge unused guards and no-write next registry candidates. It independently recomputes the reveal-intent digest.

R6 torture attacks reveal-choice transplant, old key epoch, public-key transplant, wrong origin/RP, substituted nonce-registry digest, locally rehashed approval replacing an independently retained digest, local signature-verification overclaim, physical-presence overclaim, missing nonce/challenge guard and registry-candidate effect smuggling. Control Center separately attacks nonce/challenge reuse, revoked credentials, sign-counter rollback/reuse, epoch mismatch and missing authenticator user verification.

Evidence ceiling: R6 does not claim local asymmetric signature mathematics. The external asymmetric verifier remains part of the trust root. A future production WebAuthn/public-key verifier can replace that external assertion without changing the surrounding case/nonce/key-epoch contracts.

`authenticated reveal != current truth != execution approval != trade permission`.

Observed R6 code/workflow head `0bbdf1c8c7dbdca2bd16c11ec173247f9112c808` produced run `32301131751` with `steps=null` and no job logs. Classification: `CI_BLOCKED_PRE_JOB / NOT_A_CODE_TEST_FAILURE`. Later documentation commits do not change R6 code semantics. No R6 TradingOS CI PASS is claimed and no manual rerun was requested.

Fixed ceiling: `merge=false`, `deploy=false`, `runtime=false`, `human_gate_write=false`, `credential_registry_write=false`, `nonce_registry_write=false`, `current_truth_apply=false`, `executor=false`, `signal=false`, `order=false`, `can_trade=false`, `capital_permission=DENY`.
