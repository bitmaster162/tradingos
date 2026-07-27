from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.utils.serde import to_jsonable


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(value: str) -> str:
    collapsed = _SAFE_NAME_RE.sub("_", value).strip("._")
    return collapsed or "events"


class JSONLWriter:
    def __init__(self, root_dir: Path):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._handles: dict[Path, TextIO] = {}

    def append_record(
        self,
        namespace: str,
        key: str,
        record: dict[str, Any],
        *,
        event_time_ms: int | None = None,
    ) -> Path:
        timestamp_ms = event_time_ms if event_time_ms is not None else now_ms()
        bucket = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        directory = self.root_dir / namespace / bucket
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_safe_name(key)}.jsonl"
        handle = self._handles.get(path)
        if handle is None:
            handle = path.open("a", encoding="utf-8")
            self._handles[path] = handle
        handle.write(json.dumps(to_jsonable(record), ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        return path

    def write_json(self, relative_path: str | Path, payload: Any) -> Path:
        path = self.root_dir / Path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()

    def __enter__(self) -> "JSONLWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
