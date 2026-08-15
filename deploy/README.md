# TradingOS Production Runtime v1

This directory is the deploy surface. Evidence ZIPs are not runtime inputs.

## Security model

- Container starts safely with `mode=DISABLED` / `deploy_permission=DENY`.
- Secrets and raw Telegram chat ID live only in the VPS `.env`.
- Populated `.env` must never be committed.
- Health service never sends Telegram traffic.
- Live Telegram transport additionally requires:
  1. exact R21 `AUTHORIZED_ONE_SEND_NO_EXECUTION` receipt;
  2. a new fresh R20 `ALLOW_READY_NO_SEND` receipt;
  3. exact R23 request plan + the authorized full Telegram manifest;
  4. `ENABLED/ALLOW` security config;
  5. valid runtime destination/token/HMAC environment values;
  6. `TRADINGOS_LIVE_SEND_ENABLED=1`;
  7. explicit CLI `--execution-mode LIVE_NETWORK_SEND`.
- Live send uses an atomic per-authorization claim before HTTP. If the process crashes after the claim, it fails closed and the authorization is not automatically retried.

## Claim / ledger reconciliation contract

- A claim file is evidence that an authorization entered execution. Its age alone is **not** proof that no network side effect occurred.
- Never auto-delete a stale or uncertain claim and never automatically retry the same authorization.
- If execution state is uncertain, preserve the claim and ledger exactly as found and require manual reconciliation against available transport/runtime evidence.
- If delivery cannot be proven false, treat the authorization as consumed. Any later attempt requires a new review, new exact authorization, and fresh preflight.
- Claim and ledger coordination assumes a single host/process domain using one local filesystem with atomic exclusive-create semantics. Distributed workers, shared/network filesystems, or multiple independent state directories are outside this runtime contract and require separate review before use.

## Secret lifecycle

- Telegram bot token, raw destination/chat ID, and callback HMAC secret are runtime values only; Git stores environment-variable names and destination hashes, not populated secret values.
- Keep the populated `.env` outside Git and restrict its filesystem permissions to the runtime operator/service account.
- Rotate credentials outside Git when exposure is suspected or when operational policy requires it; update the runtime `.env` and destination binding as applicable, then perform a fresh preflight before any separately authorized send.
- Do not print, persist in receipts, or paste raw runtime secrets into GitHub, logs, evidence packages, or chat.

## First VPS deployment — SAFE IDLE

This section is operational documentation only. A draft PR, branch push, or merge approval does **not** authorize deployment. Use only the exact runtime commit or tag separately approved for deployment.

```bash
git clone <your-repo-url> tradingos
cd tradingos
git checkout <approved-runtime-commit-or-tag>
cd deploy
cp .env.example .env
mkdir -p runtime/config runtime/jobs
```

Replace the safe template with the **already authorized bound security config** at:

```text
deploy/runtime/config/security_config.json
```

Populate `.env` locally on the VPS. Do not paste the bot token or HMAC secret into GitHub or chat.

Start:

```bash
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8787/healthz
```

With the current bound `DISABLED/DENY` config, expected status is:

```text
SAFE_IDLE_DISABLED
```

That is a successful first deployment: the service is online but cannot send.

## Logs / state

```bash
docker compose logs -f tradingos
```

Persistent ledgers/state are stored in the Docker volume `tradingos_state`.

## Live send

Do not run a live send until a real R21 authorization and fresh R20 preflight exist. The transport CLI is intentionally not invoked by the daemon automatically.
