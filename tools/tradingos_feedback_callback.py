#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0,str(Path(__file__).resolve().parent))
import tradingos_feedback_actions as action_tool
import tradingos_operator_impact as impact_tool

VERSION="1.0.0"; SCHEMA="tradingos.feedback_callback.receipt.v1"


def read_json(path:Path)->dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value,dict): raise ValueError("attribution must be a JSON object")
    return value


def consume(attribution:dict[str,Any],ledger:Path,action_token:str,recorded_at:str,note:str="")->dict[str,Any]:
    event_id,impact=action_tool.parse_token(action_token)
    status,row=impact_tool.record_feedback(ledger,attribution,event_id,impact,recorded_at,note)
    return {
        "schema":SCHEMA,"version":VERSION,"result":"PASS","record_status":status,"event_id":event_id,"impact":impact,
        "record_hash":row.get("record_hash"),"source":row.get("source"),
        "contract":{"explicit_callback_required":True,"token_integrity_not_authentication":True,"authentication_belongs_to_delivery_adapter":True,"network_call":False},
        "safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"},
    }


def main()->int:
    p=argparse.ArgumentParser(description="Consume one verified operator-impact callback token into the append-only explicit-feedback ledger")
    p.add_argument("--attribution",type=Path,required=True); p.add_argument("--feedback-ledger",type=Path,required=True)
    p.add_argument("--action-token",required=True); p.add_argument("--recorded-at",required=True); p.add_argument("--note",default=""); p.add_argument("--receipt",type=Path)
    a=p.parse_args()
    try:
        receipt=consume(read_json(a.attribution.resolve()),a.feedback_ledger.resolve(),a.action_token,a.recorded_at,a.note)
        if a.receipt:
            path=a.receipt.resolve(); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    print(json.dumps(receipt,ensure_ascii=False,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
