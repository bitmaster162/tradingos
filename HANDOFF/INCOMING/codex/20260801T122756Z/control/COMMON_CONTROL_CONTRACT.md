# COMMON CONTROL CONTRACT — SAFE CODEX DISPATCH R1

Authority generation: R63

## Hard invariants

- Read the exact task and evidence files in this package.
- Do not reread or rescan the full Control canter archive.
- Do not rerun Marathon M1.
- Before persistent code changes, establish a verified Git baseline:
  repo root, branch, HEAD, tree, full porcelain, remote/upstream.
- If the live root is dirty or ambiguous, work in a disposable clone.
- Do not clean/reset/stash/checkout the live root.
- Preserve exact raw stdout/stderr, argv, cwd, duration and exit codes.
- Never rewrite `READY`, `REVISE`, `COMPLETE`, `HOLD` or any producer terminal
  into a generic PASS alias.
- Never use placeholder or truncated SHA-256 values.
- No live registry/current-state/R63 mutation.
- No merge to main/master.
- No live installation or production deployment.
- No external messages.
- No exchange account, wallet, signing, order or trading effect.
- No successor work order.

## Resource ceiling

- isolated per-slot scratch root;
- maximum 4 GiB new scratch data;
- do not start install/build when free C: is below 25 GiB;
- maximum two heavy child processes;
- maximum three complete full-suite runs;
- maximum two dependency-install attempts;
- teardown every clone, venv, DB, server and child process.

## Strict return

Return exactly one triplet:

- `<TASK_ID>_RETURN_<UTC>.zip`
- `.zip.sha256`
- `.zip.READY_FOR_SYNC.json` written last

Required receipts:

- `RETURN_ENVELOPE.json`
- `TERMINAL_STATE.json`
- `GIT_IDENTITY.json`
- `COMMAND_LOG.jsonl`
- raw stdout/stderr
- `TEST_RECEIPT.json`
- `SECURITY_RECEIPT.json`
- `NO_EFFECT_RECEIPT.json`
- `TEARDOWN_RECEIPT.json`
- `MANIFEST.json`

```
can_trade=false
capital_permission=DENY
deploy_permission=DENY
self_application=false
NO_FURTHER_AGENT_WORK=true
```
