#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import tradingos_feedback_actions as feedback_tool
import tradingos_value_score as score_tool

CORE = ROOT / "tools" / "tradingos_daily_decision_run.py"


def parse_now(value: str | None) -> datetime:
    if value:
        text=value[:-1]+"+00:00" if value.endswith("Z") else value
        dt=datetime.fromisoformat(text)
        if dt.tzinfo is None: raise ValueError("now must include timezone")
        return dt.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def main() -> int:
    p=argparse.ArgumentParser(description="Run stable TradingOS daily pipeline and attach callback feedback actions plus 7/30-day Value Score")
    p.add_argument("--capture", required=True, type=Path)
    p.add_argument("--out-root", required=True, type=Path)
    p.add_argument("--now")
    p.add_argument("--memory-ledger", type=Path)
    p.add_argument("--attribution-ledger", type=Path)
    p.add_argument("--impact-ledger", type=Path)
    a=p.parse_args()
    try: now=parse_now(a.now)
    except ValueError as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    cmd=[sys.executable,str(CORE),"--capture",str(a.capture.resolve()),"--out-root",str(a.out_root.resolve())]
    if a.now: cmd += ["--now",a.now]
    for flag,value in [("--memory-ledger",a.memory_ledger),("--attribution-ledger",a.attribution_ledger),("--impact-ledger",a.impact_ledger)]:
        if value: cmd += [flag,str(value.resolve())]
    core=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True)
    if core.returncode != 0:
        sys.stdout.write(core.stdout); sys.stderr.write(core.stderr); return core.returncode
    try:
        receipt=json.loads(core.stdout)
        day_dir=a.out_root.resolve()/receipt["bangkok_day"]
        attribution_path=day_dir/receipt["outputs"]["attribution_json"]
        impact_ledger=Path(receipt["outputs"]["impact_ledger"])
        actions_payload,actions_path=feedback_tool.generate(attribution_path,day_dir/"feedback")
        as_of=now.isoformat().replace("+00:00","Z")
        score_payload,score_paths=score_tool.generate(attribution_path,impact_ledger,day_dir/"value",as_of)
        receipt["feedback_action_events"]=len(actions_payload["events"])
        receipt["value_score_windows"]=score_payload["windows"]
        receipt["outputs"]["feedback_actions"]=str(actions_path.relative_to(day_dir))
        receipt["outputs"].update({f"value_{k}":str(v.relative_to(day_dir)) for k,v in score_paths.items()})
        (day_dir/"RUN_RECEIPT.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    print(json.dumps(receipt,ensure_ascii=False,indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
