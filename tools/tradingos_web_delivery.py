#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

VERSION="1.0.0"; SCHEMA="tradingos.delivery.web.v1"


def read_json(path:Path)->dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value,dict): raise ValueError("delivery envelope must be a JSON object")
    return value


def build_manifest(envelope:dict[str,Any])->dict[str,Any]:
    if envelope.get("schema") != "tradingos.delivery.envelope.v1": raise ValueError("unsupported delivery envelope schema")
    actions=envelope.get("feedback_actions")
    if not isinstance(actions,list) or not actions: raise ValueError("missing feedback actions")
    buttons=[]
    for item in actions:
        if not isinstance(item,dict): raise ValueError("invalid feedback action")
        token=item.get("action_token")
        if not isinstance(token,str) or len(token.encode("utf-8"))>64: raise ValueError("invalid action token")
        buttons.append({"label":item.get("label"),"impact":item.get("impact"),"callback_token":token})
    return {
        "schema":SCHEMA,"version":VERSION,"mode":"DRY_RUN","delivery_id":envelope.get("delivery_id"),
        "view":{"headline":envelope.get("headline"),"body_lines":envelope.get("body_lines",[]),"buttons":buttons},
        "post_contract":{"method":"POST","path":"/operator-impact/callback","body":{"action_token":"<selected callback token>","recorded_at":"<adapter receive time UTC>","note":"<optional <=500 chars>"},"authentication":"REQUIRED_BY_DEPLOYMENT_ADAPTER_NOT_IMPLEMENTED_HERE"},
        "contract":{"preview_only":True,"network_call":False,"javascript_network_call":False,"endpoint_implemented":False,"deploy_permission":"DENY"},
        "safety":{"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY","deploy_permission":"DENY"},
    }


def render_html(manifest:dict[str,Any])->str:
    view=manifest["view"]
    body="".join(f"<p>{html.escape(str(x))}</p>" for x in view.get("body_lines",[]))
    buttons="".join(f'<button type="button" data-callback-token="{html.escape(str(x["callback_token"]))}" disabled>{html.escape(str(x["label"]))}</button>' for x in view.get("buttons",[]))
    css='*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:760px;margin:40px auto;padding:24px}.card{background:#0d1823;border:1px solid #263746;border-radius:18px;padding:22px}h1{margin:0 0 14px}p{color:#a9bac8;line-height:1.5}button{margin:6px 6px 0 0;padding:10px 13px;border-radius:10px;border:1px solid #3d5263;background:#172838;color:#d7e5ef}.dry{margin-top:18px;color:#6fdbff;font-size:12px}'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Delivery Preview</title><style>{css}</style></head><body><main><div class="card"><h1>{html.escape(str(view.get("headline")))}</h1>{body}<div>{buttons}</div><div class="dry">DRY RUN · buttons disabled · no endpoint · no network · deploy_permission=DENY</div></div></main></body></html>'


def generate(envelope_path:Path,out_dir:Path)->tuple[dict[str,Any],dict[str,Path]]:
    manifest=build_manifest(read_json(envelope_path)); out_dir.mkdir(parents=True,exist_ok=True)
    paths={"json":out_dir/"web_delivery_manifest.json","html":out_dir/"web_delivery_preview.html"}
    paths["json"].write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    paths["html"].write_text(render_html(manifest),encoding="utf-8",newline="\n")
    return manifest,paths


def main()->int:
    p=argparse.ArgumentParser(description="Render a disabled local web feedback preview and POST contract")
    p.add_argument("--envelope",type=Path,required=True); p.add_argument("--out-dir",type=Path,required=True); a=p.parse_args()
    try: manifest,paths=generate(a.envelope.resolve(),a.out_dir.resolve())
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    print(json.dumps({"result":"PASS","mode":"DRY_RUN","buttons":len(manifest["view"]["buttons"]),"outputs":{k:str(v) for k,v in paths.items()},"network_call":False,"deploy_permission":"DENY"},indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
