#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

VERSION="1.0.0"; SCHEMA="tradingos.delivery.telegram.v1"


def read_json(path:Path)->dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value,dict): raise ValueError("delivery envelope must be a JSON object")
    return value


def build(envelope:dict[str,Any])->dict[str,Any]:
    if envelope.get("schema") != "tradingos.delivery.envelope.v1": raise ValueError("unsupported delivery envelope schema")
    safety=envelope.get("safety")
    if not isinstance(safety,dict) or safety.get("can_trade") is not False or safety.get("deploy_permission") != "DENY": raise ValueError("unsafe delivery envelope")
    actions=envelope.get("feedback_actions")
    if not isinstance(actions,list) or not actions: raise ValueError("delivery envelope has no feedback actions")
    buttons=[]
    for item in actions:
        if not isinstance(item,dict): raise ValueError("invalid feedback action")
        token=item.get("action_token"); label=item.get("label")
        if not isinstance(token,str) or len(token.encode("utf-8")) > 64: raise ValueError("invalid callback_data")
        if not isinstance(label,str) or not label: raise ValueError("invalid button label")
        buttons.append({"text":label,"callback_data":token})
    rows=[buttons[:3],buttons[3:]]
    rows=[row for row in rows if row]
    text="\n".join([str(envelope.get("headline") or "TradingOS")]+[str(x) for x in envelope.get("body_lines",[])])
    if len(text) > 4096: raise ValueError("Telegram message exceeds 4096 characters")
    return {
        "schema":SCHEMA,"version":VERSION,"transport":"telegram_bot_api","mode":"DRY_RUN",
        "method":"sendMessage","request":{"text":text,"reply_markup":{"inline_keyboard":rows},"disable_web_page_preview":True},
        "callback_contract":{"handler":"tradingos_feedback_callback.py","recorded_at_source":"adapter_receive_time_utc","token_is_not_authentication":True},
        "contract":{"network_call":False,"bot_token_present":False,"chat_id_present":False,"webhook_present":False,"deploy_permission":"DENY"},
        "safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"},
    }


def generate(envelope_path:Path,out_dir:Path)->tuple[dict[str,Any],Path]:
    payload=build(read_json(envelope_path)); out_dir.mkdir(parents=True,exist_ok=True); path=out_dir/"telegram_send_message.json"
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); return payload,path


def main()->int:
    p=argparse.ArgumentParser(description="Render a dry-run Telegram sendMessage payload; never performs network I/O")
    p.add_argument("--envelope",type=Path,required=True); p.add_argument("--out-dir",type=Path,required=True); a=p.parse_args()
    try: payload,path=generate(a.envelope.resolve(),a.out_dir.resolve())
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    buttons=sum(len(r) for r in payload["request"]["reply_markup"]["inline_keyboard"])
    print(json.dumps({"result":"PASS","mode":"DRY_RUN","buttons":buttons,"output":str(path),"network_call":False,"deploy_permission":"DENY"},indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
