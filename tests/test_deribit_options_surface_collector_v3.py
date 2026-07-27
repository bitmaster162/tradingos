from __future__ import annotations

import json
from pathlib import Path

from tools import deribit_options_surface_collector_v3 as collector


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "DERIBIT_OPTIONS_SURFACE_COLLECTOR_V3.json"


def sample_market(now: int) -> tuple[list[dict], list[dict]]:
    instruments: list[dict] = []
    summaries: list[dict] = []
    for expiry_index, days in enumerate((10, 30, 60)):
        expiry = now + days * 86_400_000
        underlying = 100.0 + expiry_index * 50.0
        for strike in (underlying * 0.9, underlying, underlying * 1.1):
            for option_type, suffix in (("call", "C"), ("put", "P")):
                name = f"BTC-X{expiry_index}-{int(strike)}-{suffix}"
                instruments.append(
                    {
                        "instrument_name": name,
                        "is_active": True,
                        "expiration_timestamp": expiry,
                        "strike": strike,
                        "option_type": option_type,
                    }
                )
                summaries.append(
                    {
                        "instrument_name": name,
                        "underlying_price": underlying,
                        "mark_iv": 50.0 + expiry_index,
                        "open_interest": 10.0,
                        "bid_price": 0.01,
                        "ask_price": 0.02,
                    }
                )
    return instruments, summaries


def small_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["quality_gate"] = {
        "minimum_active_instruments": 18,
        "minimum_summary_rows": 18,
        "minimum_join_rate": 1.0,
        "minimum_mark_iv_coverage": 1.0,
        "minimum_open_interest_coverage": 1.0,
        "minimum_distinct_expiries": 3,
    }
    config["collection"]["inter_request_delay_seconds"] = 0.0
    return config


def test_reactive_refresh_repairs_fresh_but_incomplete_instrument_cache(tmp_path: Path) -> None:
    config = small_config()
    base_script, _, _ = collector.predecessor_paths(config)
    base = collector.load_base_collector(base_script)
    fixed_ms = 1_800_000_000_000
    instruments, summaries = sample_market(fixed_ms)
    base.write_gzip_json(
        tmp_path / "instruments_latest.json.gz",
        {
            "fetched_at_ms": fixed_ms - 1_000,
            "response": {"result": instruments[:12]},
        },
    )
    calls: list[str] = []

    def fetcher(url: str, _timeout: int) -> dict:
        calls.append(url)
        if "get_book_summary_by_currency" in url:
            return {"result": summaries, "usIn": 1, "usOut": 2}
        if "get_instruments" in url:
            return {"result": instruments}
        raise AssertionError(url)

    code, report = collector.collect_snapshot(
        config,
        tmp_path,
        base=base,
        fetcher=fetcher,
        clock_ms=lambda: fixed_ms,
        sleep_fn=lambda _seconds: None,
    )

    assert code == 0
    assert report["decision"] == "deribit_options_v3_surface_snapshot_healthy"
    surface = report["surface"]
    assert surface["quality_pass"] is True
    assert surface["quality"]["join_rate"] == 1.0
    assert surface["reactive_refresh_triggered"] is True
    assert surface["instrument_refresh_count"] == 1
    assert surface["instrument_refresh_reasons"] == ["join_rate_failure"]
    assert surface["predecessor_rows_admitted"] is False
    assert sum("get_instruments" in url for url in calls) == 1
    assert report["can_trade"] is False


def test_v3_lock_binds_predecessor_hash_chain() -> None:
    config = collector.read_json(CONFIG_PATH)
    lock = collector.build_lock(Path(collector.__file__).resolve(), CONFIG_PATH, config)
    passed, failures = collector.verify_lock(lock, Path(collector.__file__).resolve(), CONFIG_PATH, config)

    assert passed is True
    assert failures == []
    assert lock["predecessor_rows_admitted"] is False
    assert lock["directional_hypothesis_registered"] is False
    assert lock["can_trade"] is False
