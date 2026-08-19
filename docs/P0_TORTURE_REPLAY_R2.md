# P0 Torture / Replay R2 — Temporal Evidence + External Trust Anchor

Status: `DRAFT CANDIDATE / OFFLINE ADVERSARIAL VERIFICATION / NO EFFECT`

## Scope

R1 hardened cross-case SCT binding, freeze alignment and full SCT prediction integrity.

R2 attacks the two remaining evidence weaknesses explicitly recorded after R1:

1. core snapshot / VisionAssist evidence did not carry first-class observation and freshness proof at the replay boundary;
2. SHA-256 self-consistency alone did not establish authenticity if an attacker could rewrite and rehash the entire artifact tree.

R2 does not add another authority system. It adds a verification membrane before trusted offline replay.

```text
TradeCase v1
    +
snapshot timing / provenance
    +
Vision timing / provenance
    +
externally expected authority root
    +
externally expected case-binding hash
        ↓
tradingos.temporal_evidence_bundle.v1
        ↓
bitevo.external_replay_anchor.v1
        ↓
tradingos.shadow_temporal_replay_qualification.v1
        ↓
tradingos.trusted_replay_input.v1
        ↓
SCT / TradingOS offline replay only
```

## Temporal evidence rule

For every evidence role already present in the frozen TradeCase, R2 requires an exact source-ref match plus:

```text
observed_at
ingested_at
fresh_until
clock_verified=true
provenance_verified=true
custody_ref
```

The admissibility relation is deterministic:

```text
observed_at <= ingested_at <= TradeCase.frozen_at <= fresh_until
```

This does not impose one universal market-data TTL. `fresh_until` is supplied by the source-specific evidence policy; the membrane only enforces the declared interval and its provenance/clock proof.

If the frozen TradeCase contains VisionAssist evidence, the temporal bundle must contain the matching Vision binding. Missing or extra roles fail closed.

## External trust anchor

R2 deliberately does **not** treat a self-issued hash as trust.

The replay anchor contains:

```text
authority_id
authority_generation
authority_root_sha256
root_effective_at
case_id
case_sha256
evidence_bundle_sha256
case_binding_sha256
```

The caller must also provide three values obtained from an external trusted authority/custody channel:

```text
expected_authority_id
expected_root_sha256
expected_case_binding_sha256
```

The membrane recomputes:

```text
case_binding_sha256 =
SHA256(
  authority_id
  + authority_root_sha256
  + case_id
  + case_sha256
  + evidence_bundle_sha256
)
```

and requires it to equal the externally expected case binding.

This blocks a locally self-consistent rewrite from silently becoming the same replay case when the attacker does not control the external trusted reference.

## What R2 proves

The offline verifier can now detect:

- snapshot observed after the frozen decision time;
- snapshot stale at the frozen decision time;
- Vision evidence observed/ingested after freeze;
- missing temporal binding for a TradeCase evidence role;
- an authority root that became effective only after the case freeze;
- a rehashed anchor pointing to the wrong external root;
- a whole-case rewrite / new case ID attempting to reuse an accepted external case binding;
- modified evidence timing attempting to reuse the old accepted external case binding;
- wrong authority identity.

The qualification remains:

```text
QUALIFIED_FOR_OFFLINE_REPLAY_ONLY
```

and produces no current-truth, memory, runtime, messaging, trading or capital effect.

## Important trust ceiling

R2 reduces the full-tree-rehash problem but does not magically create source authenticity.

If an attacker controls both:

1. the local artifact tree; and
2. the supposedly external expected authority root / expected case binding,

then hash comparison cannot distinguish the forged universe.

Therefore the expected root and expected case-binding digest must come from an independently trusted authority/custody path such as an accepted Control Center / ContinuityOS / signed-manifest record.

The P0 module validates that relationship. It does not itself become the trust root.

## Safety ceiling

```text
apply_allowed=false
execution_authority=NONE
current_truth_apply=false
continuity_write=false
return_write=false
archive_write=false
runtime_activation=false
model_call=false
exchange_call=false
signal=false
order=false
capital_effect=false
can_trade=false
capital_permission=DENY
```
