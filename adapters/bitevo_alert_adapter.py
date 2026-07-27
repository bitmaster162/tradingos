"""
bitevo_alert_adapter.py
Map MAX Pipeline composite outputs into BitEvo alert schema.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _ts_iso(ts=None):
    if ts is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return str(ts)


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def to_bitevo_alert(composite, defaults=None):
    """
    composite example:
    {
      "symbol": "BTCUSDT",
      "tf": "15m",
      "setup_id": "liquidity_sweep_eq",
      "score": 0.73,
      "side": "short",
      "entry": 106900.5,
      "sl": 107520.0,
      "tp": [105800, 104400, 102900],
      "reason": "post-sweep close under EQH",
      "filters_passed": ["funding_contra", "oi_spike", "liq_cluster"],
      "metrics": {"funding_ap_7dma": 0.067},
      "bar_ts": "2025-10-19T07:10:00Z"
    }
    """
    defaults = defaults or {}
    metrics = dict(composite.get("metrics", {}))
    if "dom_bias" in composite and "dom_bias" not in metrics:
        metrics["dom_bias"] = composite["dom_bias"]

    alert = {
        "id": defaults.get("id") or str(uuid.uuid4()),
        "version": defaults.get("version") or "1.0.0",
        "ts": _ts_iso(composite.get("ts")),
        "bar_ts": composite.get("bar_ts"),
        "decision_id": composite.get("decision_id"),
        "data_degraded": bool(composite.get("data_degraded", False)),
        "symbol": composite.get("symbol"),
        "tf": composite.get("tf"),
        "setup_id": composite.get("setup_id"),
        "score": _safe_float(composite.get("score", 0.0)),
        "trigger": {
            "type": composite.get("trigger", "entry"),
            "price": _safe_float(composite.get("entry", 0.0)),
            "reason": composite.get("reason", ""),
        },
        "filters_passed": list(composite.get("filters_passed", [])),
        "metrics": metrics,
        "risk": {
            "side": composite.get("side"),
            "entry": _safe_float(composite.get("entry", 0.0)),
            "sl": _safe_float(composite.get("sl", 0.0)),
            "tp": list(composite.get("tp", [])),
            "r_multiplies": list(composite.get("r_multiplies", [1.0, 1.5, 2.5])),
            "size_hint_pct": _safe_float(composite.get("size_hint_pct", 0.5)),
            "invalidate_on": list(composite.get("invalidate_on", [])),
        },
        "links": dict(composite.get("links", {})),
        "source": {
            "pipeline": defaults.get("pipeline", "MAX"),
            "version": defaults.get("pipeline_version", "unknown"),
        },
        "latency_ms": int(composite.get("latency_ms", 0) or 0),
    }
    return alert


def to_jsonl(alerts, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for alert in alerts:
            handle.write(json.dumps(alert, ensure_ascii=False) + "\n")
    return str(target)


def to_json(alert, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(alert, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)
