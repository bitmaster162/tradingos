#!/usr/bin/env python3
from __future__ import annotations
import argparse, html, json
from pathlib import Path
from typing import Any

VERSION="1.3.0"
def read(p:Path)->dict[str,Any]:
    x=json.loads(p.read_text(encoding="utf-8-sig"));
    if not isinstance(x,dict): raise ValueError(f"{p} must contain an object")
    return x
def write(p:Path,s:str): p.write_text(s.rstrip()+"\n",encoding="utf-8",newline="\n")
def n(x:Any)->float: return float(x) if isinstance(x,(int,float)) and not isinstance(x,bool) else 0.0
def esc(x:Any)->str: return html.escape(str(x))
def hypo(b:dict[str,Any],side:str)->dict[str,Any]:
    return next((x for x in b.get("intent_hypotheses",[]) if x.get("direction")==side),{})

SAFE_PERMISSIONS={
    "read_only_analysis":True,
    "signals_allowed":False,
    "orders_allowed":False,
    "uses_credentials":False,
    "can_trade":False,
    "capital_permission":"DENY",
}
PACKET_IDENTITY=("snapshot_id","symbol","timeframe","as_of")

def validate_brief(b:dict[str,Any],label:str)->dict[str,Any]:
    if not isinstance(b,dict):
        raise ValueError(f"{label} brief must be an object")
    p=b.get("permissions")
    if not isinstance(p,dict):
        raise ValueError(f"{label} brief permissions must be an object")
    if b.get("can_trade") is not False or any(p.get(k)!=v for k,v in SAFE_PERMISSIONS.items()):
        raise ValueError(f"unsafe execution permission in {label} brief")
    provenance=b.get("provenance")
    if not isinstance(provenance,dict):
        raise ValueError(f"{label} brief provenance must be an object")
    sources=provenance.get("input_sources")
    if not isinstance(sources,list):
        raise ValueError(f"{label} brief input_sources must be a list")
    for key in PACKET_IDENTITY:
        if not isinstance(b.get(key),str) or not b[key].strip():
            raise ValueError(f"{label} brief missing identity: {key}")
    return provenance

def validate_packet(b:dict[str,Any],s:dict[str,Any],label:str)->dict[str,Any]:
    provenance=validate_brief(b,label)
    if not isinstance(s,dict):
        raise ValueError(f"{label} snapshot must be an object")
    if s.get("can_trade") is not False:
        raise ValueError(f"unsafe execution permission in {label} snapshot")
    for key in PACKET_IDENTITY:
        if not isinstance(s.get(key),str) or not s[key].strip():
            raise ValueError(f"{label} snapshot missing identity: {key}")
        if b[key] != s[key]:
            raise ValueError(f"{label} packet identity mismatch: {key}")
    return provenance

def build(b:dict[str,Any],s:dict[str,Any],pb:dict[str,Any]|None=None,ps:dict[str,Any]|None=None)->dict[str,Any]:
    if (pb is None)!=(ps is None):
        raise ValueError("previous brief and snapshot must be provided together")
    provenance=validate_packet(b,s,"current")
    if pb is not None and ps is not None:
        validate_packet(pb,ps,"previous")
    L,S=hypo(b,"LONG"),hypo(b,"SHORT"); q=b.get("uncertainty",{}); d=b.get("derivatives_context",{})
    pr=s.get("price",{}); ms=s.get("market_structure",{}); fl=s.get("flow",{}); sd=s.get("derivatives",{})
    last,sup,res=n(pr.get("last")),n(ms.get("support")),n(ms.get("resistance"))
    pressure=[]; seen=set()
    for side,h in (("LONG",L),("SHORT",S)):
        for x in h.get("supporting_evidence",[]):
            dim=x.get("dimension");
            if dim in seen: continue
            seen.add(dim); pressure.append({"label":x.get("label",dim),"direction":side,"strength":x.get("strength",0),"observation":x.get("observation","")})
    risks=[]
    if q.get("blockers"): risks.append(["BLOCK","Input gate blocked",", ".join(q["blockers"])])
    if last and res and 0 <= (res/last-1)*100 <= .5: risks.append(["WATCH","Near resistance",f"{(res/last-1)*100:.2f}% overhead"])
    if abs(n(d.get("basis_z")))>=1.5: risks.append(["WATCH","Relative basis extreme",f"z={n(d.get('basis_z')):.2f}; relative richness/cheapness, not raw-basis sign"])
    if n(fl.get("relative_volume"))<1: risks.append(["INFO","Participation below average",f"relative volume {n(fl.get('relative_volume')):.2f}x"])
    if not risks: risks=[["INFO","No elevated veto","No configured deterministic risk threshold crossed"]]
    delta={"state":"FIRST_OBSERVATION","headline":"Today becomes the comparison baseline.","changes":[]}
    if pb and ps:
        rows=[
            ["stance",pb.get("decision",{}).get("stance"),b.get("decision",{}).get("stance")],
            ["price",n(ps.get("price",{}).get("last")),last],
            ["OI Δ%",n(ps.get("derivatives",{}).get("open_interest_change_pct")),n(sd.get("open_interest_change_pct"))],
            ["funding z",n(ps.get("derivatives",{}).get("funding_z")),n(sd.get("funding_z"))],
            ["basis z",n(ps.get("derivatives",{}).get("basis_z")),n(sd.get("basis_z"))],
            ["rel volume",n(ps.get("flow",{}).get("relative_volume")),n(fl.get("relative_volume"))],
            ["score margin",n(pb.get("decision",{}).get("score_margin")),n(b.get("decision",{}).get("score_margin"))],
        ]
        delta={"state":"COMPARABLE","headline":"What changed since the last materialized packet.","changes":[{"metric":a,"from":x,"to":y} for a,x,y in rows if x!=y]}
    story=[
        f"{b.get('regime',{}).get('label')} / {b.get('decision',{}).get('stance')}; evidence margin {b.get('decision',{}).get('score_margin')}",
        f"Price {last:,.1f} inside {sup:,.1f}–{res:,.1f}; range position {n(ms.get('range_position')):.0%}",
        f"OI {n(sd.get('open_interest_change_pct')):+.2f}% · spot/perp {fl.get('spot_cvd_direction')}/{fl.get('perp_cvd_direction')} · volume {n(fl.get('relative_volume')):.2f}x",
        f"Funding z {n(sd.get('funding_z')):+.2f} · basis z {n(sd.get('basis_z')):+.2f}",
    ]
    grade="BLOCKED" if b.get("status")!="READY" else ("STRONG" if n(b.get("decision",{}).get("score_margin"))>=4 and not q.get("blockers") else "MODERATE")
    return {"schema":"tradingos.decision_cockpit.v1","version":VERSION,"brief_id":b.get("brief_id"),"symbol":b.get("symbol"),"timeframe":b.get("timeframe"),"as_of":b.get("as_of"),"status":b.get("status"),
      "executive":{"stance":b.get("decision",{}).get("stance"),"regime":b.get("regime",{}).get("label"),"volatility":b.get("regime",{}).get("volatility"),"grade":grade,"margin":b.get("decision",{}).get("score_margin"),"long":L.get("support_score",0),"short":S.get("support_score",0),"next":b.get("operator_next_action")},
      "story":story,"delta":delta,"pressure":pressure,"risk_flags":[{"severity":a,"label":b,"detail":c} for a,b,c in risks],"levels":{"last":last,"support":sup,"resistance":res,"position":ms.get("range_position"),"to_resistance_pct":round((res/last-1)*100,3) if last and res else None},"risks":risks,"scenarios":b.get("scenarios",[]),
      "quality":{"age":q.get("snapshot_age_minutes"),"missing":q.get("missing_data",[]),"conflicts":q.get("conflicts",[]),"blockers":q.get("blockers",[]),"sources":len(provenance.get("input_sources",[]))},
      "source_brief_provenance":dict(provenance),
      "safety":{"signals":False,"orders":False,"signals_allowed":False,"orders_allowed":False,"can_trade":False,"capital_permission":"DENY"}}

def render(r:dict[str,Any])->str:
    e,l,d=r["executive"],r["levels"],r["delta"]
    story="".join(f"<li>{esc(x)}</li>" for x in r["story"])
    delta="".join(f"<div class=x><span>{esc(x['metric'])}</span><b>{esc(x['from'])} → {esc(x['to'])}</b></div>" for x in d["changes"]) or '<p class=m>First packet. Delta activates on the next day.</p>'
    pressure="".join(f"<div class=x><span>{esc(x['label'])}</span><b class={x['direction'].lower()}>{esc(x['direction'])}</b><small>{esc(x['observation'])}</small></div>" for x in r["pressure"])
    risks="".join(f"<div class='risk {x[0].lower()}'><b>{esc(x[1])}</b><small>{esc(x[2])}</small></div>" for x in r["risks"])
    sc="".join(f"<article><strong>{esc(x['name']).upper()}</strong><p>{esc(x['trigger'])}</p><small>Invalidation · {esc(x['invalidation'])}</small></article>" for x in r["scenarios"])
    pos=round(n(l.get("position"))*100,1)
    return f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>TradingOS Cockpit</title><style>
:root{{--bg:#071019;--p:#0d1823;--line:#253746;--t:#eef6fb;--m:#8ea5b7;--g:#8ef58a;--a:#ffc764;--r:#ff7272;--c:#6fdbff}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 70% 0,#17344b,#071019 42%);color:var(--t);font:14px Inter,system-ui,sans-serif}}main{{max-width:1180px;margin:auto;padding:28px 18px 60px}}header{{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:18px}}h1{{font-size:56px;margin:4px 0;letter-spacing:-.05em}}.brand{{color:var(--c);font-size:11px;letter-spacing:.18em;font-weight:800}}.m,small{{color:var(--m)}}.hero,.grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin-top:14px}}.card,article{{background:linear-gradient(180deg,#10202e,#0a151f);border:1px solid var(--line);border-radius:16px;padding:18px}}.stance{{font-size:62px;font-weight:900;color:var(--g);letter-spacing:-.05em}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:15px}}.stats div{{background:#08131d;border:1px solid var(--line);padding:10px;border-radius:10px}}.stats b{{display:block;font-size:20px}}ul{{line-height:1.6;padding-left:18px}}h2{{font-size:18px;margin:0 0 12px}}.bar{{height:10px;background:#1b2b39;border-radius:9px;margin:25px 0 8px;overflow:hidden}}.bar i{{display:block;height:100%;width:{pos}%;background:linear-gradient(90deg,var(--c),var(--g))}}.levels{{display:flex;justify-content:space-between;color:var(--m)}}.last{{font-size:34px;font-weight:800}}.x{{display:grid;grid-template-columns:1fr auto;gap:8px;padding:9px 0;border-bottom:1px solid #1c2b37}}.x small{{grid-column:1/-1}}.long{{color:var(--g)}}.short{{color:var(--r)}}.risk{{border-left:3px solid var(--c);padding:10px;margin:7px 0;background:#09151f}}.risk.watch{{border-color:var(--a)}}.risk.block{{border-color:var(--r)}}.risk small{{display:block;margin-top:4px}}.sc{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}}article strong{{color:var(--c)}}footer{{margin-top:18px;border-top:1px solid var(--line);padding-top:12px;color:var(--m);font-size:11px}}@media(max-width:800px){{.hero,.grid,.sc{{grid-template-columns:1fr}}.stats{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:42px}}}}
</style></head><body><main><header><div><div class=brand>TRADINGOS · DECISION COCKPIT</div><h1>{esc(r['symbol'])}</h1></div><div class=m>{esc(r['timeframe'])} · {esc(r['as_of'])}</div></header>
<section class=hero><div class=card><div class=m>OPERATOR STATE</div><div class=stance>{esc(e['stance'])}</div><div>{esc(e['regime'])} · {esc(e['volatility'])} · evidence {esc(e['grade'])}</div><div class=stats><div><small>MARGIN</small><b>{esc(e['margin'])}</b></div><div><small>LONG</small><b>{esc(e['long'])}</b></div><div><small>SHORT</small><b>{esc(e['short'])}</b></div><div><small>STATUS</small><b>{esc(r['status'])}</b></div></div><p>{esc(e['next'])}</p></div><div class=card><h2>Market story</h2><ul>{story}</ul></div></section>
<section class=grid><div class=card><h2>Decision Delta</h2><p class=m>{esc(d['headline'])}</p>{delta}</div><div class=card><h2>Key levels</h2><div class=last>{l['last']:,.1f}</div><div class=bar><i></i></div><div class=levels><span>S {l['support']:,.1f}</span><span>{esc(l['to_resistance_pct'])}% to R</span><span>R {l['resistance']:,.1f}</span></div></div></section>
<section class=grid><div class=card><h2>Pressure Map</h2>{pressure}</div><div class=card><h2>Risk Vetoes</h2>{risks}</div></section><section class=card style="margin-top:14px"><h2>Scenario Ladder</h2><div class=sc>{sc}</div></section>
<footer>Read-only operator intelligence · sources {r['quality']['sources']} · missing {len(r['quality']['missing'])} · conflicts {len(r['quality']['conflicts'])} · blockers {len(r['quality']['blockers'])}<br>Scores are deterministic weights, not probabilities. signals=false · orders=false · can_trade=false · capital_permission=DENY.</footer></main></body></html>'''

# Compatibility names kept for tests/callers.
build_report=build
render_html=render

def generate(bp:Path,sp:Path,out:Path,pbp:Path|None=None,psp:Path|None=None)->dict[str,Path]:
    r=build(read(bp),read(sp),read(pbp) if pbp else None,read(psp) if psp else None); out.mkdir(parents=True,exist_ok=True)
    paths={"json":out/"cockpit.json","html":out/"cockpit.html","share":out/"cockpit_share.md"}
    write(paths["json"],json.dumps(r,ensure_ascii=False,indent=2)); write(paths["html"],render(r))
    write(paths["share"],f"# {r['symbol']} Decision Cockpit\n\n**{r['executive']['stance']}** · {r['executive']['regime']} · evidence {r['executive']['grade']}\n\n{r['executive']['next']}\n\n_Read-only. can_trade=false · capital_permission=DENY._")
    return paths

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--brief",type=Path,required=True); p.add_argument("--snapshot",type=Path,required=True); p.add_argument("--out-dir",type=Path,required=True); p.add_argument("--previous-brief",type=Path); p.add_argument("--previous-snapshot",type=Path); a=p.parse_args()
    try: paths=generate(a.brief,a.snapshot,a.out_dir,a.previous_brief,a.previous_snapshot)
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as x: print(json.dumps({"result":"ERROR","error":str(x),"can_trade":False})); return 2
    print(json.dumps({"result":"PASS","outputs":{k:str(v) for k,v in paths.items()},"can_trade":False,"capital_permission":"DENY"},indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
