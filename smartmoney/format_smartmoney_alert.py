import json, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def render(tpl_path, alert_path):
    tpl = Path(tpl_path).read_text(encoding='utf-8')
    a = json.loads(Path(alert_path).read_text(encoding='utf-8'))
    ctx = {
        'setup': a.get('setup_id','?'), 'tf': a.get('tf','?'), 'symbol': a.get('symbol','?'),
        'side': a.get('risk',{}).get('side','?'), 'entry': a.get('risk',{}).get('entry','?'),
        'sl': a.get('risk',{}).get('sl','?'),
        'tp_list': ', '.join(str(x) for x in a.get('risk',{}).get('tp',[])) or '-',
        'r_list': ', '.join(str(x) for x in a.get('risk',{}).get('r_multiplies',[1.0,1.5,2.5])),
        'filters': ', '.join(a.get('filters_passed',[])) or '-',
        'funding_ap': a.get('metrics',{}).get('funding_ap_7dma', 0.0),
        'oi_delta_pct': a.get('metrics',{}).get('oi_delta_pct_1h', 0.0),
        'liq_usd': a.get('metrics',{}).get('liq_cluster_usd', 0.0),
        'score': a.get('score', 0.0),
        'confirm': a.get('trigger',{}).get('reason',''),
        'smt_hint': a.get('metrics',{}).get('smt_hint','-'),
    }
    print(tpl.format(**ctx))
if __name__ == '__main__':
    render(sys.argv[1], sys.argv[2])
