from __future__ import annotations
import importlib.util, json
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MOD=ROOT/"tools"/"tradingos_operator_7d_status.py"
spec=importlib.util.spec_from_file_location("operator7d",MOD); assert spec and spec.loader
operator7d=importlib.util.module_from_spec(spec); spec.loader.exec_module(operator7d)


def write_packet(root: Path, day: str, brief_id: str, stance: str="WATCH_LONG"):
    d=root/day; d.mkdir(parents=True)
    receipt={"bangkok_day":day,"brief_id":brief_id,"can_trade":False}
    brief={"brief_id":brief_id,"status":"READY","decision":{"stance":stance},"uncertainty":{"blockers":[],"conflicts":[],"missing_data":[]},"operator_next_action":"watch","can_trade":False}
    (d/"RUN_RECEIPT.json").write_text(json.dumps(receipt),encoding="utf-8")
    (d/"brief.json").write_text(json.dumps(brief),encoding="utf-8")


def test_incomplete_window_and_zero_pilots(tmp_path: Path):
    daily=tmp_path/"daily"; write_packet(daily,"2026-08-09","b1")
    out=operator7d.build(daily,tmp_path/"pilot.jsonl",date(2026,8,9))
    assert out["window"]["materialized_days"]==1
    assert len(out["window"]["missing_days"])==6
    assert out["risk_gates"]["daily_completeness"]=="INCOMPLETE"
    assert out["mvp"]["status"]=="INSUFFICIENT_PILOTS"
    assert out["can_trade"] is False


def test_seven_days_and_three_paid_passes_mvp(tmp_path: Path):
    daily=tmp_path/"daily"
    for n,day in enumerate(["2026-08-03","2026-08-04","2026-08-05","2026-08-06","2026-08-07","2026-08-08","2026-08-09"]):
        write_packet(daily,day,f"b{n}","WATCH_LONG" if n%2==0 else "NO_ACTION")
    ledger=tmp_path/"pilot.jsonl"
    rows=[{"pilot_id":f"p{i}","event":"paid" if i<3 else "used","amount_usd":199 if i<3 else 0} for i in range(5)]
    ledger.write_text("".join(json.dumps(r)+"\n" for r in rows),encoding="utf-8")
    out=operator7d.build(daily,ledger,date(2026,8,9))
    assert out["window"]["materialized_days"]==7
    assert out["risk_gates"]["daily_completeness"]=="PASS"
    assert out["mvp"]["status"]=="MVP_PASS"
    assert out["terminal"]=="MVP_PASS"
