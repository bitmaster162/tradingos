import pandas as pd, numpy as np
def _hh_ll_flags(c,h,l,lookback=50):
    import pandas as pd
    rh=h.rolling(lookback).max(); rl=l.rolling(lookback).min();
    return (h>=rh.shift(1)).astype(int),(l<=rl.shift(1)).astype(int)
def smt_breadth_score(ohlcv_map, leader='BTCUSDT', lookback=50):
    dfs=[]
    for k,df in ohlcv_map.items():
        d=df[['high','low','close']].rename(columns=lambda c:f'{k}_{c}'); dfs.append(d)
    import pandas as pd
    c=pd.concat(dfs,axis=1,join='inner').dropna(); flags={}
    for s in ohlcv_map:
        hh,ll=_hh_ll_flags(c[f'{s}_close'], c[f'{s}_high'], c[f'{s}_low'], lookback)
        flags[s]={'hh':hh,'ll':ll}
    last=c.index[-1]; L_hh=int(flags[leader]['hh'].loc[last]); L_ll=int(flags[leader]['ll'].loc[last])
    followers=[s for s in ohlcv_map if s!=leader]
    n_hh=sum(int(flags[s]['hh'].loc[last])==0 for s in followers) if L_hh==1 else 0
    n_ll=sum(int(flags[s]['ll'].loc[last])==0 for s in followers) if L_ll==1 else 0
    breadth=0.0; breadth -= n_hh/max(1,len(followers)); breadth += n_ll/max(1,len(followers))
    return {'timestamp':str(last),'leader':leader,'breadth':float(breadth)}
