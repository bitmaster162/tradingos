#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
SCHEMA = "tradingos.delivery.envelope.v1"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _safe(payload: dict[str, Any], name: str) -> None:
    safety = payload.get("safety")
    if not isinstance(safety, dict):
        raise ValueError(f"{name} missing safety contract")
    if safety.get("can_trade") is not False or safety.get("capital_permission") != "DENY":
        raise ValueError(f"{name} violates read-only safety contract")
    for key in ("signals_allowed", "orders_allowed"):
        if key in safety and safety.get(key) is not False:
            raise ValueError(f"{name} violates {key}=false")


def _actions(actions: dict[str, Any], event_id: str) -> list[dict[str, str]]:
    if actions.get("schema") != "tradingos.operator_impact.actions.v1":
        raise ValueError("unsupported feedback actions schema")
    rows = actions.get("events")
    if not isinstance(rows, list):
        raise ValueError("feedback actions events must be a list")
    for row in rows:
        if isinstance(row, dict) and row.get("event_id") == event_id:
            items = row.get("actions")
            if not isinstance(items, list) or not items:
                raise ValueError("feedback action event has no actions")
            result=[]
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("invalid feedback action")
                token=item.get("action_token"); label=item.get("label"); impact=item.get("impact")
                if not all(isinstance(x,str) and x for x in (token,label,impact)):
                    raise ValueError("invalid feedback action fields")
                if len(token.encode("utf-8")) > 64:
                    raise ValueError("feedback token exceeds transport limit")
                result.append({"impact":impact,"label":label,"action_token":token})
            return result
    raise ValueError("event has no feedback actions")


def build(alert: dict[str, Any], cockpit: dict[str, Any], attribution: dict[str, Any], actions: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    if alert.get("schema") != "tradingos.decision_alert.v1": raise ValueError("unsupported alert schema")
    if cockpit.get("schema") != "tradingos.decision_cockpit.v1": raise ValueError("unsupported cockpit schema")
    if attribution.get("schema") != "tradingos.value_attribution.report.v1": raise ValueError("unsupported attribution schema")
    if value.get("schema") != "tradingos.value_score.report.v1": raise ValueError("unsupported value score schema")
    for name,payload in (("alert",alert),("cockpit",cockpit),("attribution",attribution),("actions",actions),("value",value)):
        _safe(payload,name)
    if alert.get("decision") != "NOTIFY":
        raise ValueError("delivery envelope is only emitted for NOTIFY alerts")
    events=attribution.get("events")
    if not isinstance(events,list) or not events:
        raise ValueError("attribution has no events to deliver")
    alert_events=alert.get("events")
    if not isinstance(alert_events,list) or not alert_events:
        raise ValueError("alert has no events")
    primary=events[-1]
    if not isinstance(primary,dict) or not isinstance(primary.get("event_id"),str):
        raise ValueError("invalid primary attribution event")
    feedback=_actions(actions,primary["event_id"])
    executive=cockpit.get("executive") if isinstance(cockpit.get("executive"),dict) else {}
    levels=cockpit.get("levels") if isinstance(cockpit.get("levels"),dict) else {}
    ae=alert_events[0] if isinstance(alert_events[0],dict) else {}
    windows=value.get("windows") if isinstance(value.get("windows"),dict) else {}
    def score_window(name: str) -> dict[str, Any]:
        row=windows.get(name)
        if not isinstance(row,dict): raise ValueError(f"missing {name} value window")
        return {"score":row.get("score"),"grade":row.get("grade"),"evidence_gaps":row.get("evidence_gaps",[])}
    lines=[
        f'{alert.get("symbol")} · {alert.get("priority")} · {executive.get("stance")}',
        str(ae.get("title") or alert.get("level_state") or "TradingOS attention event"),
        str(ae.get("detail") or ""),
        f'4h last {levels.get("last")} · support {levels.get("support")} · resistance {levels.get("resistance")}',
        f'Next: {alert.get("next_action")}',
        'Read-only decision support · no signal · no order',
    ]
    return {
        "schema":SCHEMA,"version":VERSION,
        "delivery_id":f'{alert.get("dedupe_key")}:{primary["event_id"]}',
        "created_from":{"brief_id":alert.get("brief_id"),"alert_dedupe_key":alert.get("dedupe_key"),"event_id":primary["event_id"]},
        "symbol":alert.get("symbol"),"priority":alert.get("priority"),"stance":executive.get("stance"),"outcome":primary.get("outcome"),
        "headline":lines[0],"body_lines":lines[1:],"feedback_actions":feedback,
        "value_proof":{"7d":score_window("7d"),"30d":score_window("30d")},
        "contract":{"render_only":True,"feedback_write":False,"network_call":False,"credentials_required":False,"delivery_requires_explicit_adapter":True},
        "safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"},
    }


def generate(alert_path:Path,cockpit_path:Path,attribution_path:Path,actions_path:Path,value_path:Path,out_dir:Path)->tuple[dict[str,Any],Path]:
    payload=build(read_json(alert_path),read_json(cockpit_path),read_json(attribution_path),read_json(actions_path),read_json(value_path))
    out_dir.mkdir(parents=True,exist_ok=True); path=out_dir/"delivery_envelope.json"
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    return payload,path


def main()->int:
    p=argparse.ArgumentParser(description="Build a transport-neutral read-only TradingOS delivery envelope")
    for flag in ("alert","cockpit","attribution","actions","value"):
        p.add_argument(f"--{flag}",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True); a=p.parse_args()
    try: payload,path=generate(a.alert.resolve(),a.cockpit.resolve(),a.attribution.resolve(),a.actions.resolve(),a.value.resolve(),a.out_dir.resolve())
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    print(json.dumps({"result":"PASS","delivery_id":payload["delivery_id"],"actions":len(payload["feedback_actions"]),"output":str(path),"can_trade":False,"deploy_permission":"DENY"},indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
