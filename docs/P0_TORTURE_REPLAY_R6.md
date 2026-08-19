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

TradingOS independently requires:

- same case id/SHA;
- same DecisionPacket SHA;
- same SCT prediction id;
- same reveal choice and time;
- same human subject and custody provider policy;
- exact credential id and public-key digest;
- exact algorithm and key epoch;
- exact verifier id/key id;
- exact origin and RP id;
- external asymmetric signature-verifier assertion present;
- `local_signature_math_verified=false`;
- authenticator user-present and user-verified flags present;
- credential active/epoch guards present;
- nonce and challenge unused guards present;
- exact no-write next credential/nonce registry candidates;
- no execution authority.

The reveal-intent digest is independently recomputed by TradingOS.

## Adversarial matrix

R6 includes attacks against:

- reveal choice transplant;
- old key epoch;
- public-key transplant;
- wrong origin / RP id;
- substituted nonce-registry digest;
- locally rehashed approval replacing an independently retained approval digest;
- local signature-verification overclaim;
- physical-presence overclaim;
- missing nonce/challenge replay guard;
- effect smuggling through next registry candidates.

Control Center separately tests nonce reuse, used challenge ids, revoked credentials, sign-counter rollback/reuse, epoch mismatch, user-verification absence and assertion effect smuggling.

## Evidence ceiling

R6 does not claim local asymmetric signature mathematics. The trusted external verifier remains part of the trust root. A future production path may replace that assertion with independently verified WebAuthn/public-key proof without changing the surrounding case/nonce/key-epoch contracts.

Even a valid asymmetric custody closure remains evidence only:

`authenticated reveal != current truth != approval to execute != trade permission`.

Fixed ceiling:

`merge=false`, `deploy=false`, `runtime=false`, `human_gate_write=false`, `credential_registry_write=false`, `nonce_registry_write=false`, `current_truth_apply=false`, `executor=false`, `signal=false`, `order=false`, `can_trade=false`, `capital_permission=DENY`.
