from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Callable

USER_TRADES_DATASET = "user_trades"
INCOME_HISTORY_DATASET = "income_history"
_DATASETS = {USER_TRADES_DATASET, INCOME_HISTORY_DATASET}
_MS_PER_DAY = 24 * 60 * 60 * 1000
_ZERO = Decimal("0")


@dataclass(slots=True)
class ArchiveLoadResult:
    rows: list[dict[str, object]]
    gaps: list[tuple[int, int]]
    coverage_ratio: Decimal
    covered_ms: int
    requested_ms: int
    source_mode: str


class AuthoritativeArchive:
    def __init__(self, root_dir: Path, *, symbol: str) -> None:
        self.root_dir = Path(root_dir)
        self.symbol = symbol.upper()
        self.symbol_key = self.symbol.lower()

    def manifest_path(self) -> Path:
        return self.root_dir / "authoritative" / "manifests" / f"{self.symbol_key}_history_manifest.json"

    def dataset_path(self, dataset: str, bucket: str) -> Path:
        self._validate_dataset(dataset)
        return self.root_dir / "authoritative" / dataset / bucket / f"{self.symbol_key}.jsonl"

    def load_manifest(self) -> dict[str, object]:
        path = self.manifest_path()
        if not path.exists():
            return {
                "symbol": self.symbol,
                "updated_at_ms": 0,
                "datasets": {
                    USER_TRADES_DATASET: {},
                    INCOME_HISTORY_DATASET: {},
                },
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_authoritative_manifest")
        payload.setdefault("symbol", self.symbol)
        payload.setdefault("updated_at_ms", 0)
        datasets = payload.setdefault("datasets", {})
        if not isinstance(datasets, dict):
            raise ValueError("invalid_authoritative_manifest_datasets")
        datasets.setdefault(USER_TRADES_DATASET, {})
        datasets.setdefault(INCOME_HISTORY_DATASET, {})
        return payload

    def save_manifest(self, manifest: dict[str, object]) -> Path:
        path = self.manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def upsert_rows(
        self,
        dataset: str,
        rows: list[dict[str, object]],
        *,
        coverage_intervals: list[tuple[int, int]],
        updated_at_ms: int,
    ) -> dict[str, int]:
        self._validate_dataset(dataset)
        counts_by_bucket: dict[str, int] = {}
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            bucket = _bucket_for_ms(_row_time_ms(row))
            grouped.setdefault(bucket, []).append(row)

        key_fn = _key_fn_for_dataset(dataset)
        sort_fn = _sort_fn_for_dataset(dataset)
        for bucket, bucket_rows in grouped.items():
            path = self.dataset_path(dataset, bucket)
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = _read_jsonl_rows(path)
            merged: dict[tuple[object, ...], dict[str, object]] = {}
            for row in existing:
                merged[key_fn(row)] = row
            for row in bucket_rows:
                merged[key_fn(row)] = row
            sorted_rows = sorted(merged.values(), key=sort_fn)
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in sorted_rows),
                encoding="utf-8",
            )
            counts_by_bucket[bucket] = len(sorted_rows)

        manifest = self.load_manifest()
        datasets = manifest.setdefault("datasets", {})
        dataset_manifest = datasets.setdefault(dataset, {})
        if not isinstance(dataset_manifest, dict):
            raise ValueError("invalid_dataset_manifest")

        for start_ms, end_ms in coverage_intervals:
            for bucket, coverage_start_ms, coverage_end_ms in iter_bucket_intersections(start_ms, end_ms):
                bucket_manifest = dataset_manifest.setdefault(bucket, {})
                if not isinstance(bucket_manifest, dict):
                    bucket_manifest = {}
                    dataset_manifest[bucket] = bucket_manifest
                intervals = bucket_manifest.setdefault("intervals", [])
                if not isinstance(intervals, list):
                    intervals = []
                normalized = _merge_intervals(intervals, coverage_start_ms, coverage_end_ms)
                bucket_manifest["intervals"] = normalized
                bucket_manifest["last_updated_ms"] = updated_at_ms
                if bucket in counts_by_bucket:
                    bucket_manifest["row_count"] = counts_by_bucket[bucket]
                else:
                    bucket_manifest.setdefault("row_count", 0)
        manifest["updated_at_ms"] = updated_at_ms
        self.save_manifest(manifest)
        return counts_by_bucket

    def load_rows_for_range(self, dataset: str, *, start_ms: int, end_ms: int) -> ArchiveLoadResult:
        self._validate_dataset(dataset)
        if end_ms < start_ms:
            return ArchiveLoadResult(rows=[], gaps=[], coverage_ratio=_ZERO, covered_ms=0, requested_ms=0, source_mode="no_range")

        manifest = self.load_manifest()
        dataset_manifest = manifest.get("datasets", {}).get(dataset, {})
        if not isinstance(dataset_manifest, dict):
            dataset_manifest = {}
        gaps: list[tuple[int, int]] = []
        for bucket, bucket_start_ms, bucket_end_ms in iter_bucket_intersections(start_ms, end_ms):
            bucket_manifest = dataset_manifest.get(bucket, {})
            intervals = bucket_manifest.get("intervals", []) if isinstance(bucket_manifest, dict) else []
            gaps.extend(_bucket_gaps(intervals, bucket_start_ms, bucket_end_ms))
        gaps = _coalesce_gaps(gaps)

        key_fn = _key_fn_for_dataset(dataset)
        sort_fn = _sort_fn_for_dataset(dataset)
        merged: dict[tuple[object, ...], dict[str, object]] = {}
        for bucket in iter_buckets(start_ms, end_ms):
            path = self.dataset_path(dataset, bucket)
            if not path.exists():
                continue
            for row in _read_jsonl_rows(path):
                row_time_ms = _row_time_ms(row)
                if row_time_ms < start_ms or row_time_ms > end_ms:
                    continue
                merged[key_fn(row)] = row
        rows = sorted(merged.values(), key=sort_fn)

        requested_ms = max(0, end_ms - start_ms + 1)
        gap_ms = sum(max(0, gap_end_ms - gap_start_ms + 1) for gap_start_ms, gap_end_ms in gaps)
        covered_ms = max(0, requested_ms - gap_ms)
        coverage_ratio = (Decimal(covered_ms) / Decimal(requested_ms)) if requested_ms > 0 else _ZERO
        if not rows and gaps:
            source_mode = "archive_missing"
        elif gaps:
            source_mode = "archive_partial"
        else:
            source_mode = "archive_complete"
        return ArchiveLoadResult(
            rows=rows,
            gaps=gaps,
            coverage_ratio=coverage_ratio,
            covered_ms=covered_ms,
            requested_ms=requested_ms,
            source_mode=source_mode,
        )

    @staticmethod
    def _validate_dataset(dataset: str) -> None:
        if dataset not in _DATASETS:
            raise ValueError(f"unsupported_dataset:{dataset}")


def iter_buckets(start_ms: int, end_ms: int) -> list[str]:
    if end_ms < start_ms:
        return []
    current = _utc_dt_from_ms(start_ms).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = _utc_dt_from_ms(end_ms).replace(hour=0, minute=0, second=0, microsecond=0)
    buckets: list[str] = []
    while current <= end_dt:
        buckets.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return buckets


def iter_bucket_intersections(start_ms: int, end_ms: int) -> list[tuple[str, int, int]]:
    results: list[tuple[str, int, int]] = []
    for bucket in iter_buckets(start_ms, end_ms):
        day_start_ms, day_end_ms = bucket_bounds_ms(bucket)
        results.append((bucket, max(start_ms, day_start_ms), min(end_ms, day_end_ms)))
    return results


def bucket_bounds_ms(bucket: str) -> tuple[int, int]:
    day_start = datetime.strptime(bucket, "%Y-%m-%d").replace(tzinfo=UTC)
    day_start_ms = int(day_start.timestamp() * 1000)
    return day_start_ms, day_start_ms + _MS_PER_DAY - 1


def _bucket_for_ms(timestamp_ms: int) -> str:
    return _utc_dt_from_ms(timestamp_ms).strftime("%Y-%m-%d")


def _utc_dt_from_ms(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)


def _row_time_ms(row: dict[str, object]) -> int:
    return int(row.get("time", row.get("trade_time_ms", row.get("event_time_ms", 0))) or 0)


def _merge_intervals(existing: list[object], start_ms: int, end_ms: int) -> list[list[int]]:
    intervals: list[tuple[int, int]] = []
    for item in existing:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            intervals.append((int(item[0]), int(item[1])))
    intervals.append((int(start_ms), int(end_ms)))
    intervals.sort(key=lambda item: (item[0], item[1]))
    merged: list[list[int]] = []
    for item_start_ms, item_end_ms in intervals:
        if not merged or item_start_ms > merged[-1][1] + 1:
            merged.append([item_start_ms, item_end_ms])
        else:
            merged[-1][1] = max(merged[-1][1], item_end_ms)
    return merged


def _bucket_gaps(existing: list[object], start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    normalized = _merge_intervals(existing, start_ms + 1, start_ms) if existing else []
    gaps: list[tuple[int, int]] = []
    cursor = start_ms
    for interval_start_ms, interval_end_ms in normalized:
        if interval_end_ms < start_ms or interval_start_ms > end_ms:
            continue
        interval_start_ms = max(interval_start_ms, start_ms)
        interval_end_ms = min(interval_end_ms, end_ms)
        if interval_start_ms > cursor:
            gaps.append((cursor, interval_start_ms - 1))
        cursor = max(cursor, interval_end_ms + 1)
        if cursor > end_ms:
            break
    if cursor <= end_ms:
        gaps.append((cursor, end_ms))
    return gaps


def _coalesce_gaps(gaps: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not gaps:
        return []
    sorted_gaps = sorted(gaps, key=lambda item: (item[0], item[1]))
    merged: list[tuple[int, int]] = [sorted_gaps[0]]
    for start_ms, end_ms in sorted_gaps[1:]:
        prev_start_ms, prev_end_ms = merged[-1]
        if start_ms <= prev_end_ms + 1:
            merged[-1] = (prev_start_ms, max(prev_end_ms, end_ms))
        else:
            merged.append((start_ms, end_ms))
    return merged


def _read_jsonl_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _user_trade_key(row: dict[str, object]) -> tuple[object, ...]:
    trade_id = int(row.get("id", row.get("tradeId", row.get("trade_id", 0))) or 0)
    if trade_id > 0:
        return ("trade", trade_id)
    return (
        "fallback",
        int(row.get("orderId", row.get("order_id", 0)) or 0),
        int(row.get("time", row.get("trade_time_ms", 0)) or 0),
        str(row.get("price", "")),
        str(row.get("qty", row.get("quantity", ""))),
        str(row.get("realizedPnl", row.get("realized_pnl", ""))),
    )


def _income_key(row: dict[str, object]) -> tuple[object, ...]:
    return (
        str(row.get("incomeType", "")),
        str(row.get("tranId", "")),
        str(row.get("tradeId", "")),
        str(row.get("symbol", "")),
        int(row.get("time", 0) or 0),
        str(row.get("income", "")),
    )


def _key_fn_for_dataset(dataset: str) -> Callable[[dict[str, object]], tuple[object, ...]]:
    if dataset == USER_TRADES_DATASET:
        return _user_trade_key
    return _income_key


def _sort_fn_for_dataset(dataset: str) -> Callable[[dict[str, object]], tuple[object, ...]]:
    if dataset == USER_TRADES_DATASET:
        return lambda row: (
            int(row.get("time", row.get("trade_time_ms", 0)) or 0),
            int(row.get("id", row.get("tradeId", row.get("trade_id", 0))) or 0),
        )
    return lambda row: (
        int(row.get("time", 0) or 0),
        str(row.get("tranId", "")),
        str(row.get("tradeId", "")),
    )
