#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import tradingos_delivery_guard as guard

V = "1.0.0"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict): raise ValueError("security config must be object")
    return value


def snapshot(config: dict[str, Any], environ: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    validated = guard.validate(config)
    bindings = validated["destination_bindings"]
    base = {
        "schema": "tradingos.runtime.health.v1", "version": V,
        "mode": validated["mode"], "deploy_permission": validated["deploy_permission"],
        "binding_count": len(bindings), "network_call": False, "send_performed": False,
        "secrets_persisted": False, "raw_destination_persisted": False,
        "can_trade": False, "capital_permission": "DENY",
    }
    if validated["mode"] == "DISABLED":
        return {**base, "status": "SAFE_IDLE_DISABLED", "ready_for_authorized_send": False, "blockers": ["MODE_DISABLED"]}
    if validated["deploy_permission"] != "ALLOW":
        return {**base, "status": "SAFE_IDLE_DEPLOY_DENY", "ready_for_authorized_send": False, "blockers": ["DEPLOY_PERMISSION_DENY"]}
    if len(bindings) != 1:
        return {**base, "status": "BLOCKED_BINDING", "ready_for_authorized_send": False, "blockers": ["EXACTLY_ONE_BINDING_REQUIRED"]}
    alias = next(iter(bindings))
    try:
        rt = guard.runtime(validated, alias, env, True)
    except ValueError as exc:
        return {**base, "status": "BLOCKED_RUNTIME_INPUTS", "ready_for_authorized_send": False, "destination_alias": alias, "blockers": [str(exc)]}
    return {
        **base, "status": "READY_FOR_AUTHORIZED_SEND", "ready_for_authorized_send": True,
        "destination_alias": alias,
        "runtime": {"destination_bound": True, "bot_present": bool(rt["bot_present"]), "hmac_secret_present": bool(rt["secret_present"]), "values_persisted": False},
        "blockers": [],
    }


def serve(config_path: Path, bind: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return
        def do_GET(self) -> None:
            if self.path not in {"/healthz", "/readyz"}:
                self.send_response(404); self.end_headers(); return
            try:
                payload = snapshot(read_json(config_path))
                code = 200 if self.path == "/healthz" else (200 if payload["ready_for_authorized_send"] or payload["status"].startswith("SAFE_IDLE") else 503)
            except Exception as exc:
                payload = {"schema": "tradingos.runtime.health.v1", "version": V, "status": "CONFIG_ERROR", "error": str(exc), "ready_for_authorized_send": False, "network_call": False, "send_performed": False, "can_trade": False}
                code = 503
            body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer((bind, port), Handler)
    server.serve_forever()


def main() -> int:
    p = argparse.ArgumentParser(description="TradingOS fail-closed runtime health service; never sends network traffic to Telegram")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("health"); q.add_argument("--config", type=Path, required=True)
    s = sub.add_parser("serve"); s.add_argument("--config", type=Path, required=True); s.add_argument("--bind", default="0.0.0.0"); s.add_argument("--port", type=int, default=8787)
    a = p.parse_args()
    try:
        if a.cmd == "health":
            payload = snapshot(read_json(a.config.resolve()))
            print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0 if payload["status"] != "CONFIG_ERROR" else 2
        serve(a.config.resolve(), a.bind, a.port); return 0
    except Exception as exc:
        print(json.dumps({"status":"CONFIG_ERROR","error":str(exc),"network_call":False,"send_performed":False,"can_trade":False}, indent=2)); return 2

if __name__ == "__main__": raise SystemExit(main())
