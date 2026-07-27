def dynamic_risk_pct(base_pct, z_basis=None, z_funding=None, dom_bias=0.0, clamp=(0.25,1.5)):
    mult=1.0
    if z_basis is not None: mult*=1.0/(1.0+0.25*abs(z_basis))
    if z_funding is not None: mult*=1.0/(1.0+0.25*abs(z_funding))
    mult*=(1.0+0.15*dom_bias)
    mult=max(clamp[0],min(clamp[1],mult))
    return round(base_pct*mult,6)
