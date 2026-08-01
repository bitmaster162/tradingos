"""Shared deterministic contract helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FULL_SHA256_LENGTH = 64


class ContractError(ValueError):
    """A stable fail-closed contract violation."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "REJECTED",
            "error_code": self.code,
            "message": self.message,
            "details": self.details,
        }


def canonical_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(
            "NON_CANONICAL_JSON_VALUE",
            "canonical JSON must contain only finite JSON-compatible values",
        ) from exc
    return (rendered + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_full_sha256(value: Any, field: str) -> str:
    text = str(value or "").lower()
    if len(text) != FULL_SHA256_LENGTH or any(char not in "0123456789abcdef" for char in text):
        raise ContractError(
            "INVALID_SHA256",
            f"{field} must be a complete lowercase SHA-256 value",
            {"field": field},
        )
    return text


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(
            "INVALID_JSON_INPUT", f"cannot load JSON: {path}", {"path": str(path)}
        ) from exc


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def parse_utc(value: Any, field: str) -> datetime:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("INVALID_TIMESTAMP", f"{field} is not ISO-8601", {"field": field}) from exc
    if parsed.tzinfo is None:
        raise ContractError("NAIVE_TIMESTAMP", f"{field} must include timezone", {"field": field})
    return parsed.astimezone(timezone.utc)


def require_fields(value: dict[str, Any], fields: list[str], scope: str) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise ContractError(
            "MISSING_REQUIRED_FIELDS",
            f"{scope} is incomplete",
            {"scope": scope, "missing": missing},
        )


def stable_result(status: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, **fields}
