#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import tradingos_delivery_envelope as envelope_tool
import tradingos_telegram_delivery as telegram_tool
import tradingos_web_delivery as web_tool

CORE=ROOT/"tools"/"tradingos_daily_value_run.py"


def main()->int:
    p=argparse.ArgumentParser(description="Run stable TradingOS value pipeline and render dry-run Telegram/web delivery artifacts")
    p.add_argument("--capture",required=True,type=Path); p.add_argument("--out-root",required=True,type=Path); p.add_argument("--now")
    p.add_argument("--memory-ledger",type=Path); p.add_argument("--attribution-ledger",type=Path); p.add_argument("--impact-ledger",type=Path); a=p.parse_args()
    cmd=[sys.executable,str(CORE),"--capture",str(a.capture.resolve()),"--out-root",str(a.out_root.resolve())]
    if a.now: cmd += ["--now",a.now]
    for flag,value in (("--memory-ledger",a.memory_ledger),("--attribution-ledger",a.attribution_ledger),("--impact-ledger",a.impact_ledger)):
        if value: cmd += [flag,str(value.resolve())]
    core=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True)
    if core.returncode != 0:
        sys.stdout.write(core.stdout); sys.stderr.write(core.stderr); return core.returncode
    try:
        receipt=json.loads(core.stdout); day_dir=a.out_root.resolve()/receipt["bangkok_day"]; outputs=receipt["outputs"]
        envelope,envelope_path=envelope_tool.generate(day_dir/outputs["alert_json"],day_dir/outputs["cockpit_json"],day_dir/outputs["attribution_json"],day_dir/outputs["feedback_actions"],day_dir/outputs["value_json"],day_dir/"delivery")
        telegram,telegram_path=telegram_tool.generate(envelope_path,day_dir/"delivery"/"telegram")
        web,web_paths=web_tool.generate(envelope_path,day_dir/"delivery"/"web")
        receipt["delivery"]={"mode":"DRY_RUN","delivery_id":envelope["delivery_id"],"telegram_buttons":sum(len(r) for r in telegram["request"]["reply_markup"]["inline_keyboard"]),"web_buttons":len(web["view"]["buttons"]),"network_call":False,"deploy_permission":"DENY"}
        outputs["delivery_envelope"]=str(envelope_path.relative_to(day_dir)); outputs["telegram_payload"]=str(telegram_path.relative_to(day_dir)); outputs.update({f"web_delivery_{k}":str(v.relative_to(day_dir)) for k,v in web_paths.items()})
        (day_dir/"RUN_RECEIPT.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    print(json.dumps(receipt,ensure_ascii=False,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
