from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MOD=ROOT/"tools"/"tradingos_pilot_mvp_status.py"
spec=importlib.util.spec_from_file_location("mvp",MOD); assert spec and spec.loader
mvp=importlib.util.module_from_spec(spec); spec.loader.exec_module(mvp)

def test_empty_is_insufficient():
    out=mvp.evaluate([]); assert out["status"]=="INSUFFICIENT_PILOTS"; assert out["qualifying_pilots"]==0

def test_three_of_five_passes():
    rows=[{"pilot_id":f"p{i}","event":"paid" if i<3 else "used","amount_usd":199 if i<3 else 0} for i in range(5)]
    out=mvp.evaluate(rows); assert out["status"]=="MVP_PASS"; assert out["qualifying_pilots"]==3

def test_underpayment_does_not_qualify():
    rows=[{"pilot_id":"p1","event":"paid","amount_usd":198}]
    assert mvp.evaluate(rows)["qualifying_pilots"]==0
