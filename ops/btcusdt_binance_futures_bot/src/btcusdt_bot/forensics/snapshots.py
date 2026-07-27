from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class ForensicsSnapshot:
    event_time_ms: int
    symbol: str
    action_type: str
    state_before: str
    state_after: str
    payload: Any
    decision: str = ""
    reason: str = ""
    active_entry_client_id: str = ""
    market_messages: int = 0
    tags: tuple[str, ...] = field(default_factory=tuple)


class ForensicsRecorder:
    def __init__(self, writer: JSONLWriter, *, symbol: str) -> None:
        self.writer = writer
        self.symbol = symbol

    def record_snapshot(
        self,
        *,
        action_type: str,
        payload: Any,
        event_time_ms: int,
        state_before: str,
        state_after: str,
        decision: str = "",
        reason: str = "",
        active_entry_client_id: str = "",
        market_messages: int = 0,
        tags: tuple[str, ...] = (),
    ) -> Path:
        snapshot = ForensicsSnapshot(
            event_time_ms=event_time_ms,
            symbol=self.symbol,
            action_type=action_type,
            state_before=state_before,
            state_after=state_after,
            payload=payload,
            decision=decision,
            reason=reason,
            active_entry_client_id=active_entry_client_id,
            market_messages=market_messages,
            tags=tags,
        )
        return self.writer.append_record(
            "forensics",
            f"{self.symbol.lower()}_snapshots",
            snapshot,
            event_time_ms=event_time_ms,
        )
