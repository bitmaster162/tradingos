from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode


def now_ms() -> int:
    return int(time.time() * 1000)


def encode_params(params: dict[str, object]) -> str:
    filtered = {k: v for k, v in params.items() if v is not None}
    return urlencode(filtered, doseq=True)


def sign_payload(payload: str, secret: str) -> str:
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256)
    return signature.hexdigest()


def build_signed_query(params: dict[str, object], secret: str) -> str:
    payload = encode_params(params)
    signature = sign_payload(payload, secret)
    return f"{payload}&signature={signature}" if payload else f"signature={signature}"
