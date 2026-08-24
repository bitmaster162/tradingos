"""TradingOS R79 deterministic transport envelope construction and validation."""
from __future__ import annotations

import hashlib
from typing import Any

from tools.tradingos_model_transport_common import *
from tools.tradingos_model_transport_common import _label, _ID24_RE, _SHA64_RE

def build_transport_envelope(
    request: dict[str, Any],
    prompt: str,
    transport_policy: dict[str, Any],
    *,
    provider_id: str,
    model_id: str,
    transport_mode: str = ALLOWED_MODE,
) -> dict[str, Any]:
    validate_transport_policy(transport_policy)
    provider_id = _label(provider_id, "provider_id", 64)
    model_id = _label(model_id, "model_id", 128)
    if transport_mode != ALLOWED_MODE:
        raise ValueError("live/unknown transport mode denied")
    if not isinstance(prompt, str) or not prompt or len(prompt) > transport_policy["max_prompt_chars"]:
        raise ValueError("prompt invalid or exceeds limit")

    request_sha = stable_sha256(request)
    prompt_sha = text_sha256(prompt)
    policy_sha = stable_sha256(transport_policy)

    request_id = request.get("request_id")
    brief = request.get("brief")
    if not isinstance(request_id, str) or _ID24_RE.fullmatch(request_id) is None:
        raise ValueError("request_id invalid")
    if not isinstance(brief, dict):
        raise ValueError("request brief missing")
    brief_sha = brief.get("brief_sha256")
    if not isinstance(brief_sha, str) or _SHA64_RE.fullmatch(brief_sha) is None:
        raise ValueError("brief_sha256 invalid")

    provider = {
        "provider_id": provider_id,
        "model_id": model_id,
        "transport_mode": transport_mode,
    }
    envelope_id = hashlib.sha256(
        f"{ENVELOPE_SCHEMA}:{VERSION}:{request_id}:{brief_sha}:{request_sha}:{prompt_sha}:{policy_sha}:"
        f"{provider_id}:{model_id}:{transport_mode}".encode("utf-8")
    ).hexdigest()[:24]

    envelope = {
        "schema": ENVELOPE_SCHEMA,
        "version": VERSION,
        "envelope_id": envelope_id,
        "request_id": request_id,
        "brief_sha256": brief_sha,
        "request_sha256": request_sha,
        "prompt_sha256": prompt_sha,
        "transport_policy_sha256": policy_sha,
        "provider": provider,
        "prompt": prompt,
        "safety": dict(SAFETY),
    }
    validate_transport_envelope(envelope, request, prompt, transport_policy)
    return envelope


def validate_transport_envelope(
    envelope: Any,
    request: dict[str, Any],
    prompt: str,
    transport_policy: dict[str, Any],
) -> None:
    validate_transport_policy(transport_policy)
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_KEYS:
        raise ValueError("transport envelope key set mismatch")
    if envelope.get("schema") != ENVELOPE_SCHEMA or envelope.get("version") != VERSION:
        raise ValueError("unsupported transport envelope")
    if envelope.get("safety") != SAFETY:
        raise ValueError("transport safety drift")
    provider = envelope.get("provider")
    if not isinstance(provider, dict) or set(provider) != PROVIDER_KEYS:
        raise ValueError("provider descriptor key set mismatch")
    _label(provider.get("provider_id"), "provider_id", 64)
    _label(provider.get("model_id"), "model_id", 128)
    if provider.get("transport_mode") != ALLOWED_MODE:
        raise ValueError("live/unknown transport mode denied")
    if envelope.get("request_sha256") != stable_sha256(request):
        raise ValueError("request binding mismatch")
    if envelope.get("prompt_sha256") != text_sha256(prompt):
        raise ValueError("prompt binding mismatch")
    if envelope.get("transport_policy_sha256") != stable_sha256(transport_policy):
        raise ValueError("transport policy binding mismatch")
    if envelope.get("prompt") != prompt:
        raise ValueError("prompt bytes mismatch")
    if envelope.get("request_id") != request.get("request_id"):
        raise ValueError("request_id binding mismatch")
    brief = request.get("brief")
    if not isinstance(brief, dict) or envelope.get("brief_sha256") != brief.get("brief_sha256"):
        raise ValueError("brief binding mismatch")

    expected_id = hashlib.sha256(
        f"{ENVELOPE_SCHEMA}:{VERSION}:{envelope['request_id']}:{envelope['brief_sha256']}:"
        f"{envelope['request_sha256']}:{envelope['prompt_sha256']}:{envelope['transport_policy_sha256']}:"
        f"{provider['provider_id']}:{provider['model_id']}:{provider['transport_mode']}".encode("utf-8")
    ).hexdigest()[:24]
    if envelope.get("envelope_id") != expected_id:
        raise ValueError("envelope_id mismatch")


__all__ = ["build_transport_envelope", "validate_transport_envelope"]
