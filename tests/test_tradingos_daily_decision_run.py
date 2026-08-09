from __future__ import annotations
import importlib.util, json, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIXTURE_MOD=ROOT/"tests"/"test_tradingos_binance_public_snapshot.py"
spec=importlib.util.spec_from_file_location("fixture_mod",FIXTURE_MOD); assert spec and spec.loader
fixture_mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture_mod)
SCRIPT=ROOT/"tools"/"tradingos_daily_decision_run.py"


def test_one_packet_per_bangkok_day(tmp_path: Path):
    capture=tmp_path/"capture.json"; capture.write_text(json.dumps(fixture_mod.fixture()),encoding="utf-8")
    out=tmp_path/"daily"
    cmd=[sys.executable,str(SCRIPT),"--capture",str(capture),"--out-root",str(out),"--now","2026-08-09T16:05:00Z"]
    first=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True); assert first.returncode==0, first.stderr+first.stdout
    payload=json.loads(first.stdout); assert payload["bangkok_day"]=="2026-08-09" and payload["can_trade"] is False
    assert payload["memory_append_status"]=="APPENDED" and payload["memory_sequence"]==1
    assert set(payload["memory_windows"].values())=={"INSUFFICIENT_HISTORY"}
    ledger=out/"market_memory.ndjson"; assert ledger.is_file() and len(ledger.read_text(encoding="utf-8").splitlines())==1
    assert (out/"2026-08-09"/"memory"/"market_replay.html").is_file()
    second=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True); assert second.returncode==4
    assert json.loads(second.stdout)["result"]=="DUPLICATE_DAY_SUPPRESSED"
    assert len(ledger.read_text(encoding="utf-8").splitlines())==1
