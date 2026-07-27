from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class WSMessage:
    stream: str | None
    payload: dict[str, Any]
    raw: dict[str, Any]

    @property
    def event_type(self) -> str:
        return str(self.payload.get("e", ""))


class WSMessageDecodeError(ValueError):
    pass


RawWSPayload = str | bytes | dict[str, Any]


def decode_ws_message(raw_message: RawWSPayload) -> WSMessage:
    if isinstance(raw_message, bytes):
        try:
            raw_message = raw_message.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WSMessageDecodeError("websocket payload is not valid utf-8") from exc

    if isinstance(raw_message, str):
        try:
            parsed: Any = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise WSMessageDecodeError("websocket payload is not valid json") from exc
    else:
        parsed = raw_message

    if not isinstance(parsed, dict):
        raise WSMessageDecodeError("websocket payload is not a json object")

    if "stream" in parsed and "data" in parsed:
        payload = parsed["data"]
        if not isinstance(payload, dict):
            raise WSMessageDecodeError("combined websocket payload missing object data")
        return WSMessage(stream=str(parsed["stream"]), payload=payload, raw=parsed)

    return WSMessage(stream=None, payload=parsed, raw=parsed)
