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

p=load("r95_bench_gate",ROOT/"tools"/"r6_liquidity_persistence_gate.py")
r84=load("r95_bench_r84",ROOT/"tools"/"r6_all16_admission_probe.py")
SCHEMA="tradingos.r95_full_equity_persistence_benchmark.v1"

async def run(symbols:list[str])->dict[str,Any]:
    rows=[]
    for symbol in symbols:
        try:
            rules=await asyncio.to_thread(p.cap.fetch_symbol_rules,symbol)
            seq=await p.capture_persistence_sequence(symbol)
            result=p.benchmark_full_equity_persistence(seq,rules)
            states=seq["states"]
            rows.append({"symbol":symbol,"event_span_ms":seq["event_span_ms"],
                         "state_count":len(states),"restart_count":seq.get("restart_count",0),
                         "first_event_time_ms":states[0]["quote"]["event_time_ms"],
                         "final_event_time_ms":states[-1]["quote"]["event_time_ms"],"result":result})
        except Exception as exc:
            rows.append({"symbol":symbol,"event_span_ms":None,"state_count":0,"restart_count":None,
                         "first_event_time_ms":None,"final_event_time_ms":None,
                         "result":p.fail("PERSISTENCE_CAPTURE_FAIL_CLOSED",error=str(exc))})
    return {"schema":SCHEMA,"benchmark_notional_usdt":str(p.cap.RESEARCH_EQUITY_USDT),
            "required_states":p.REQUIRED_STATES,"min_event_span_ms":p.MIN_EVENT_SPAN_MS,
            "max_event_gap_ms":p.MAX_EVENT_GAP_MS,"max_event_span_ms":p.MAX_EVENT_SPAN_MS,
            "impact_limit_bps":str(p.MAX_IMPACT_BPS),"symbol_count":len(rows),
            "pass_count":sum(x["result"].get("status")=="PASS" for x in rows),
            "fail_count":sum(x["result"].get("status")!="PASS" for x in rows),
            "results":rows,"can_trade":False,"capital_permission":"DENY"}

def main()->int:
    ap=argparse.ArgumentParser(description="Run R95 full-equity liquidity-persistence stress benchmark")
    ap.add_argument("--all16",action="store_true"); ap.add_argument("--symbol",action="append",dest="symbols")
    ap.add_argument("--output",type=Path); args=ap.parse_args()
    symbols=list(r84.ALL16) if args.all16 else (args.symbols or ["BTCUSDT"])
    try:
        result=asyncio.run(run(symbols)); text=json.dumps(result,sort_keys=True,separators=(",",":"),allow_nan=False)
        if args.output: args.output.write_text(text+"\n",encoding="utf-8")
        print(text); return 0 if result["fail_count"]==0 else 3
    except Exception as exc:
        print(json.dumps({"schema":SCHEMA,"result":"BENCHMARK_FAIL_CLOSED","error":str(exc),
                          "can_trade":False,"capital_permission":"DENY"},sort_keys=True,separators=(",",":")))
        return 2

if __name__=="__main__": raise SystemExit(main())
