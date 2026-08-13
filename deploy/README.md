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
