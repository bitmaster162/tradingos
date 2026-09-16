#!/usr/bin/env python3
from __future__ import annotations

import argparse, asyncio, importlib.util, json, sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]

def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

r95=load("r96_bench_r95",ROOT/"tools"/"r6_persistent_capacity_sweep.py")
cont=load("r96_bench_gate",ROOT/"tools"/"r6_decision_continuity_gate.py")
sweep=load("r96_bench_sweep",ROOT/"tools"/"r6_decision_continuity_sweep.py")
SCHEMA="tradingos.r96_decision_continuity_benchmark.v1"


def candidate(symbol,direction,old_q,qty):
    px=float(old_q["ask"] if direction=="LONG" else old_q["bid"])
    decision="ENTER_LONG_AT_R85_DECISION_QUOTE" if direction=="LONG" else "ENTER_SHORT_AT_R92_DECISION_QUOTE"
    row={"Symbol":symbol[:-4],"CURRENT_Decision":decision,
         "CURRENT_Stop":px*0.9 if direction=="LONG" else px*1.1,
         "CURRENT_Target":px*1.1 if direction=="LONG" else px*0.9,
         "CURRENT_Trigger_Spec":"{}","Operational_Risk":"PASS","Notes":"BENCHMARK_ONLY"}
    return {"registration_candidate":True,"ledger_write_authority":False,"capacity_gate_pass":True,
            "liquidity_persistence_gate_pass":True,"row_values":row,
            "execution_capacity":{"status":"PASS","quantity":qty,
                                  "quote_provenance_sha256":old_q["provenance_sha256"]},
            "can_trade":False,"capital_permission":"DENY"}

async def run(symbols:list[str])->dict[str,Any]:
    rows=[]
    refs={"BTCUSDT":r95.r94.r93.r92.base.r84.fetch_ctha("BTCUSDT"),
          "ETHUSDT":r95.r94.r93.r92.base.r84.fetch_ctha("ETHUSDT")}
    for symbol in symbols:
        try:
            _,_,rules,_,seq=await r95._capture_symbol(symbol,refs)
            pbench=r95.persist.benchmark_full_equity_persistence(seq,rules)
            if pbench.get("status")!="PASS":
                rows.append({"symbol":symbol,"upstream_persistence_stress":"FAIL_CLOSED",
                             "reason":pbench.get("reason"),"long":None,"short":None})
                continue
            old_q=seq["states"][-1]["quote"]
            fresh_q,fresh_b,fresh_rules,fresh_ref=await sweep._fresh_continuity_state(symbol,rules)
            long=cont.evaluate_continuity(candidate(symbol,"LONG",old_q,pbench["buy_quantity"]),
                                          old_q,fresh_q,fresh_b,fresh_rules,fresh_ref)
            short=cont.evaluate_continuity(candidate(symbol,"SHORT",old_q,pbench["sell_quantity"]),
                                           old_q,fresh_q,fresh_b,fresh_rules,fresh_ref)
            rows.append({"symbol":symbol,"upstream_persistence_stress":"PASS","long":long,"short":short})
        except Exception as exc:
            rows.append({"symbol":symbol,"upstream_persistence_stress":"CAPTURE_FAIL_CLOSED",
                         "reason":f"{type(exc).__name__}:{exc}","long":None,"short":None})
    evaluated=sum(r["upstream_persistence_stress"]=="PASS" for r in rows)
    return {"schema":SCHEMA,"symbol_count":len(rows),"evaluated_symbol_count":evaluated,
            "long_pass_count":sum((r.get("long") or {}).get("status")=="PASS" for r in rows),
            "short_pass_count":sum((r.get("short") or {}).get("status")=="PASS" for r in rows),
            "results":rows,"benchmark_only":True,"ledger_write_authority":False,
            "can_trade":False,"capital_permission":"DENY"}

def main()->int:
    ap=argparse.ArgumentParser(description="Run R96 decision-continuity benchmark")
    ap.add_argument("--all16",action="store_true"); ap.add_argument("--symbol",action="append",dest="symbols")
    ap.add_argument("--output",type=Path); args=ap.parse_args()
    all16=list(r95.r94.r93.r92.base.r84.ALL16)
    symbols=all16 if args.all16 else (args.symbols or ["BTCUSDT"])
    result=asyncio.run(run(symbols)); text=json.dumps(result,sort_keys=True,separators=(",",":"),allow_nan=False)
    if args.output: args.output.write_text(text+"\n",encoding="utf-8")
    print(text); return 0

if __name__=="__main__": raise SystemExit(main())
