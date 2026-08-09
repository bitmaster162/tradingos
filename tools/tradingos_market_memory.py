#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradingos_market_memory_state import diff_states, extract_state, observed_at, parse_time, sha, time_text

VERSION = "1.0.0"
RECORD_SCHEMA = "tradingos.market_memory.record.v1"
REPLAY_SCHEMA = "tradingos.market_replay.v1"
GENESIS = "GENESIS"
WINDOWS = (("1h", timedelta(hours=1)), ("4h", timedelta(hours=4)), ("24h", timedelta(hours=24)))


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict): raise ValueError(f"{path} must contain a JSON object")
    return value


def verify_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists(): return []
    records: list[dict[str, Any]] = []; previous_hash = GENESIS; previous_time = None
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip(): continue
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("schema") != RECORD_SCHEMA: raise ValueError(f"ledger line {line_no}: invalid record")
        if value.get("sequence") != len(records) + 1: raise ValueError(f"ledger line {line_no}: non-contiguous sequence")
        if value.get("prev_record_hash") != previous_hash: raise ValueError(f"ledger line {line_no}: prev_record_hash mismatch")
        claimed = value.get("record_hash"); body = dict(value); body.pop("record_hash", None)
        if not isinstance(claimed, str) or sha(body) != claimed: raise ValueError(f"ledger line {line_no}: record_hash mismatch")
        observed = parse_time(str(value.get("observed_at")))
        if previous_time is not None and observed <= previous_time: raise ValueError(f"ledger line {line_no}: observed_at is not strictly increasing")
        state = value.get("state")
        if not isinstance(state, dict) or value.get("state_fingerprint") != sha(state): raise ValueError(f"ledger line {line_no}: state_fingerprint mismatch")
        records.append(value); previous_hash, previous_time = claimed, observed
    return records


def append_observation(ledger: Path, radar: dict[str, Any] | None = None, cockpit: dict[str, Any] | None = None, alert: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    records = verify_ledger(ledger); when = observed_at(radar, cockpit, alert); when_dt = parse_time(when); state = extract_state(radar, cockpit, alert); fingerprint = sha(state)
    if records:
        last = records[-1]; last_dt = parse_time(last["observed_at"])
        if when_dt < last_dt: raise ValueError("non-monotonic observation; historical backfill is disabled")
        if when_dt == last_dt:
            if last.get("state_fingerprint") == fingerprint: return "DUPLICATE_SUPPRESSED", last, records
            raise ValueError("same observed_at with different state")
        change, prev_hash = diff_states(last["state"], state), last["record_hash"]
    else:
        change, prev_hash = {"material_change": False, "change_count": 0, "changes": [], "summary": "BASELINE_ESTABLISHED"}, GENESIS
    body = {
        "schema": RECORD_SCHEMA, "version": VERSION, "sequence": len(records) + 1, "observed_at": when, "prev_record_hash": prev_hash,
        "state_fingerprint": fingerprint, "change_from_previous": change, "state": state,
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }
    record = dict(body); record["record_hash"] = sha(body); ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as f: f.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    records.append(record); return "APPENDED", record, records


def build_replay(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records: raise ValueError("cannot replay an empty ledger")
    current = records[-1]; now = parse_time(current["observed_at"]); windows: dict[str, Any] = {}
    for label, delta in WINDOWS:
        cutoff = now - delta; candidates = [r for r in records[:-1] if parse_time(r["observed_at"]) <= cutoff]
        if not candidates:
            windows[label] = {"status": "INSUFFICIENT_HISTORY", "requested_cutoff": time_text(cutoff), "baseline_observed_at": None, "actual_span_hours": None, "delta": None}; continue
        base = candidates[-1]; span = (now - parse_time(base["observed_at"])).total_seconds() / 3600.0
        windows[label] = {"status": "COMPARABLE", "requested_cutoff": time_text(cutoff), "baseline_sequence": base["sequence"], "baseline_observed_at": base["observed_at"], "actual_span_hours": round(span, 4), "delta": diff_states(base["state"], current["state"])}
    return {
        "schema": REPLAY_SCHEMA, "version": VERSION, "current_sequence": current["sequence"], "current_observed_at": current["observed_at"], "current_record_hash": current["record_hash"],
        "ledger_records": len(records), "latest_change": current["change_from_previous"], "windows": windows,
        "contract": {"append_only": True, "tamper_evident_hash_chain": True, "historical_backfill_disabled": True, "window_baseline": "nearest real observation at or before requested cutoff", "insufficient_history_is_not_fabricated": True},
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def render_html(replay: dict[str, Any]) -> str:
    cards=[]
    for label in ("1h","4h","24h"):
        row=replay["windows"][label]
        body=(f'<b>{html.escape(row["delta"]["summary"])}</b><span>{row["delta"]["change_count"]} changes · span {row["actual_span_hours"]}h</span>' if row["status"]=="COMPARABLE" else '<b>INSUFFICIENT_HISTORY</b><span>No historical state is fabricated.</span>')
        cards.append(f'<article><small>{label} REPLAY</small>{body}</article>')
    latest=replay["latest_change"]; rows="".join(f'<li><b>{html.escape(str(x.get("scope")))}</b> · {html.escape(str(x.get("field")))}</li>' for x in latest.get("changes",[])[:12]) or "<li>No material change from previous observation.</li>"
    css='*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1080px;margin:auto;padding:28px}.k{font-size:11px;letter-spacing:.16em;color:#6fdbff;font-weight:800}h1{font-size:48px;letter-spacing:-2px;margin:5px 0}.sub,small,span{color:#8fa5b7}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}article,.panel{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px}article b,article span{display:block;margin-top:8px}ul{line-height:1.8;padding-left:20px}@media(max-width:700px){.grid{grid-template-columns:1fr}h1{font-size:38px}}'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Market Replay</title><style>{css}</style></head><body><main><div class="k">TRADINGOS · MARKET MEMORY</div><h1>Change Replay</h1><div class="sub">Observation #{replay["current_sequence"]} · {html.escape(replay["current_observed_at"])} · hash-chain verified</div><div class="grid">{"".join(cards)}</div><section class="panel"><small>LATEST TRANSITION</small><h2>{html.escape(latest["summary"])}</h2><ul>{rows}</ul></section><p class="sub">Append-only · no historical backfill · signals=false · orders=false · can_trade=false · capital_permission=DENY</p></main></body></html>'


def generate(ledger: Path, out_dir: Path, radar_path: Path | None = None, cockpit_path: Path | None = None, alert_path: Path | None = None) -> tuple[str, dict[str, Path], dict[str, Any]]:
    radar, cockpit, alert = (_read(radar_path) if radar_path else None), (_read(cockpit_path) if cockpit_path else None), (_read(alert_path) if alert_path else None)
    status, record, records = append_observation(ledger, radar, cockpit, alert); replay = build_replay(records); out_dir.mkdir(parents=True, exist_ok=True)
    paths={"record":out_dir/"latest_record.json","replay":out_dir/"market_replay.json","html":out_dir/"market_replay.html"}
    paths["record"].write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); paths["replay"].write_text(json.dumps(replay,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); paths["html"].write_text(render_html(replay),encoding="utf-8",newline="\n")
    return status, paths, replay


def main() -> int:
    p=argparse.ArgumentParser(description="Append safe market state to tamper-evident memory and render 1h/4h/24h replay"); p.add_argument("--radar",type=Path); p.add_argument("--cockpit",type=Path); p.add_argument("--alert",type=Path); p.add_argument("--ledger",type=Path,required=True); p.add_argument("--out-dir",type=Path,required=True); a=p.parse_args()
    try: status,paths,replay=generate(a.ledger.resolve(),a.out_dir.resolve(),a.radar.resolve() if a.radar else None,a.cockpit.resolve() if a.cockpit else None,a.alert.resolve() if a.alert else None)
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc: print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    print(json.dumps({"result":"PASS","append_status":status,"sequence":replay["current_sequence"],"windows":{k:v["status"] for k,v in replay["windows"].items()},"outputs":{k:str(v) for k,v in paths.items()},"can_trade":False,"capital_permission":"DENY"},indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
