# P0 TORTURE / REPLAY R5 — Human Reveal Custody

Status: DRAFT CANDIDATE / SHADOW ONLY / NO EFFECT

R1-R4 prove case, temporal, history and domain-subject integrity. R5 addresses the remaining distinction:

```text
correct reveal artifact
!=
trusted human/session custody evidence
```

## Cross-plane path

```text
Control Center challenge
  case + packet + Twin + options
  human subject + session + device
  nonce + issued_at + expires_at
        ↓
external custody attestation
  HMAC-SHA256 verifier-key possession
  exact challenge + nonce
  exact session/device/provider
  selected choice + response time
        ↓
Control Center verification
  expected registry digest
  unused challenge check
  next single-use registry candidate (NO WRITE)
        ↓
control_center.shadow_human_approval_verification.v1
        ↓
TradingOS R4 reveal + domain history
        ↓
bitevo.shadow_authenticated_reveal_closure.v1
        ↓
Control Center non-authority projection
```

## New R5 contracts

Control Center owns challenge/custody verification:

- `control_center.shadow_human_approval_challenge.v1`
- `control_center.shadow_human_custody_attestation.v1`
- `control_center.shadow_human_approval_registry_snapshot.v1`
- `control_center.shadow_human_approval_verification.v1`

TradingOS owns exact reveal/domain rebinding:

- `bitevo.shadow_authenticated_reveal_closure.v1`

## Challenge binding

The challenge binds:

- exact `case_id` and `case_sha256`;
- exact DecisionPacket SHA;
- exact SCT prediction id;
- frozen option set including `WAIT`;
- expected human subject id;
- session id;
- device id;
- custody provider id;
- nonce;
- timezone-aware issue/expiry window.

The challenge scope is only `HUMAN_REVEAL_ONLY`. It explicitly does not authorize execution.

## Custody cryptography ceiling

R5 verifies an HMAC-SHA256 attestation produced with externally held verifier key material. The Control Center candidate code receives verifier key material only as a verification input; no key is stored in current truth, repository state, registry, or receipt.

What this proves:

`possession of the trusted custody verifier key for the exact challenge payload`.

What it does **not** prove:

- biometric physical presence;
- legal identity;
- sole control of a device;
- that no upstream custody provider is compromised;
- execution permission.

Therefore all R5 receipts keep:

```text
human_identity_scope=CUSTODY_PROVIDER_SUBJECT_ASSERTION_ONLY
physical_human_presence_proven=false
execution_authority=NONE
can_execute=false
```

## Anti-replay

Control Center requires an externally retained approval-registry digest. A challenge id already present in that exact registry is rejected.

For an unused challenge, R5 creates a **next registry candidate only** containing:

- challenge id;
- challenge SHA;
- attestation SHA.

No registry write is performed under P0. Therefore the precise claim is:

`single-use candidate verified against expected external registry`

not:

`durable single-use enforcement is live`.

## TradingOS reveal binding

TradingOS independently requires the verified approval to match the exact R4 reveal on:

- case;
- packet;
- Twin prediction;
- selected option;
- response/decision time.

Both planes independently derive the same reveal-intent digest from those fields.

TradingOS also requires:

- R4 subject manifest still binds that reveal SHA;
- R4 domain-history closure still binds that subject manifest;
- externally retained exact approval-verification digest;
- expected human subject / provider / verifier / verifier-key policy.

## Torture classes

R5 rejects:

- wrong verifier key / invalid HMAC;
- expired challenge response;
- challenge replay against expected registry;
- session transplant;
- device transplant;
- choice outside frozen options;
- packet transplant;
- reveal-choice transplant;
- verifier-key policy mismatch;
- physical-presence overclaim;
- human-gate effect smuggling;
- registry candidate not binding the same challenge/attestation;
- locally rehashed approval replacing the independently expected approval digest.

## Fixed ceiling

```text
merge=false
deploy=false
runtime_activation=false
human_gate_write=false
registry_write=false
current_truth_apply=false
executor_dispatch=false
signal=false
order=false
capital_effect=false
execution_authority=NONE
can_trade=false
capital_permission=DENY
```
