#!/usr/bin/env python3
from __future__ import annotations
import argparse, asyncio, importlib.util, json, sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None: raise RuntimeError(f'cannot load {name}')
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod
r95=load('r97_bench_r95',ROOT/'tools'/'r6_persistent_capacity_sweep.py')
r96=load('r97_bench_r96',ROOT/'tools'/'r6_decision_continuity_gate.py')
r96s=load('r97_bench_r96s',ROOT/'tools'/'r6_decision_continuity_sweep.py')
settle=load('r97_bench_settle',ROOT/'tools'/'r6_paper_fill_settlement.py')
cap=r95.r94.cap
SCHEMA='tradingos.r97_paper_fill_settlement_benchmark.v1'

def candidate(symbol:str,direction:str,quote:dict[str,Any],cost:dict[str,Any])->dict[str,Any]:
    px=float(quote['ask'] if direction=='LONG' else quote['bid'])
    stop=px*0.99 if direction=='LONG' else px*1.01
    target=px*1.02 if direction=='LONG' else px*0.98
    decision='ENTER_LONG_AT_R85_DECISION_QUOTE' if direction=='LONG' else 'ENTER_SHORT_AT_R92_DECISION_QUOTE'
    trigger={'quote_provenance_sha256':quote['provenance_sha256'],'settlement_spec':{
        'entry_fee_rate':cost['entry_fee_rate'],'exit_fee_rate':cost['exit_fee_rate'],
        'entry_slippage_bps':cost['entry_slippage_bps'],'exit_slippage_bps':cost['exit_slippage_bps'],
        'initial_planned_risk_per_unit':'1'}}
    row={'Symbol':symbol[:-4],'CURRENT_Decision':decision,'CURRENT_Entry_Price':px,
         'CURRENT_Stop':stop,'CURRENT_Target':target,'CURRENT_Trigger_Spec':json.dumps(trigger,sort_keys=True,separators=(',',':')),
         'Operational_Risk':'BENCHMARK_ONLY','Provenance_State':'R97_BENCHMARK_ONLY','Notes':'R97_BENCHMARK_ONLY',
         'CURRENT_Entry_At_UTC':'','Registered_At_UTC':'','RR_Net':''}
    return {'event_id':f'R97_BENCH_{symbol}_{direction}','registration_candidate':True,
            'ledger_write_authority':False,'row_values':row,'can_trade':False,'capital_permission':'DENY'}

def attach_upstream(c:dict[str,Any],capacity:dict[str,Any],persistence:dict[str,Any])->dict[str,Any]:
    out=dict(c); out['execution_capacity']=capacity; out['capacity_gate_pass']=True
    out['liquidity_persistence']=persistence; out['liquidity_persistence_gate_pass']=True
    return out

def attach_continuity(c:dict[str,Any],continuity:dict[str,Any])->dict[str,Any]:
    out=dict(c); out['decision_continuity']=continuity; out['decision_continuity_gate_pass']=True
    return out

async def run(symbols:list[str],runtime:dict[str,Any])->dict[str,Any]:
    refs={'BTCUSDT':r95.r94.r93.r92.base.r84.fetch_ctha('BTCUSDT'),
          'ETHUSDT':r95.r94.r93.r92.base.r84.fetch_ctha('ETHUSDT')}
    rows=[]
    for symbol in symbols:
        try:
            _,book,rules,reference,seq=await r95._capture_symbol(symbol,refs)
            old_q=seq['states'][-1]['quote']
            integ=r95.r94.r93.gate.evaluate(old_q,reference,final_time_ms=reference['integrity_final_time_ms']) if isinstance(reference,dict) else {'status':'FAIL_CLOSED'}
            if integ.get('status')!='PASS':
                rows.append({'symbol':symbol,'integrity':'FAIL_CLOSED','reason':integ.get('reason'),'long':None,'short':None}); continue
            fresh_q,fresh_b,fresh_rules,fresh_ref=await r96s._fresh_continuity_state(symbol,rules)
            bydir={}
            for direction in ('LONG','SHORT'):
                c=candidate(symbol,direction,old_q,runtime['cost_model'])
                capacity=cap.evaluate_capacity(c,runtime,old_q,book,rules)
                if capacity.get('status')!='PASS':
                    bydir[direction.lower()]={'stage':'R94','status':'FAIL_CLOSED','reason':capacity.get('reason')}; continue
                persistence=r95.persist.evaluate_persistence(c,capacity,seq)
                if persistence.get('status')!='PASS':
                    bydir[direction.lower()]={'stage':'R95','status':'FAIL_CLOSED','reason':persistence.get('reason')}; continue
                up=attach_upstream(c,capacity,persistence)
                continuity=r96.evaluate_continuity(up,old_q,fresh_q,fresh_b,fresh_rules,fresh_ref)
                if continuity.get('status')!='PASS':
                    bydir[direction.lower()]={'stage':'R96','status':'FAIL_CLOSED','reason':continuity.get('reason')}; continue
                settled=settle.settle_candidate(attach_continuity(up,continuity),runtime)
                s=settled.get('paper_fill_settlement',{})
                bydir[direction.lower()]={'stage':'R97','status':s.get('status',settled.get('status')),
                    'reason':s.get('reason',settled.get('reason')),
                    'quantity':capacity.get('quantity'),'old_entry':c['row_values']['CURRENT_Entry_Price'],
                    'fresh_depth_vwap':continuity.get('fresh_entry_depth_vwap'),'settled_entry':s.get('settled_entry_price'),
                    'actual_risk_usdt':s.get('actual_planned_risk_usdt'),'risk_budget_usdt':s.get('risk_budget_usdt'),
                    'net_rr':s.get('net_rr')}
            rows.append({'symbol':symbol,'integrity':'PASS','long':bydir.get('long'),'short':bydir.get('short')})
        except Exception as exc:
            rows.append({'symbol':symbol,'integrity':'CAPTURE_FAIL_CLOSED','reason':f'{type(exc).__name__}:{exc}','long':None,'short':None})
    return {'schema':SCHEMA,'symbol_count':len(rows),
            'long_pass_count':sum((x.get('long') or {}).get('status')=='PASS' for x in rows),
            'short_pass_count':sum((x.get('short') or {}).get('status')=='PASS' for x in rows),
            'results':rows,'benchmark_only':True,'ledger_write_authority':False,'can_trade':False,'capital_permission':'DENY'}

def main()->int:
    ap=argparse.ArgumentParser(description='Run R97 paper-fill settlement benchmark')
    ap.add_argument('--runtime-state',required=True,type=Path); ap.add_argument('--all16',action='store_true')
    ap.add_argument('--symbol',action='append',dest='symbols'); ap.add_argument('--output',type=Path)
    args=ap.parse_args(); runtime=json.loads(args.runtime_state.read_text(encoding='utf-8-sig'))
    all16=list(r95.r94.r93.r92.base.r84.ALL16); symbols=all16 if args.all16 else (args.symbols or ['BTCUSDT'])
    result=asyncio.run(run(symbols,runtime)); text=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False)
    if args.output: args.output.write_text(text+'\n',encoding='utf-8')
    print(text); return 0

if __name__=='__main__': raise SystemExit(main())
