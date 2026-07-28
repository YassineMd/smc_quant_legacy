import os, sys; sys.path.insert(0, os.getcwd())
import numpy as np
import study.absorb_engulf_bias_15m as S
import study.mom_absorb_1h as MA

tf = "15m"
S.precompute(tf)
X, KB, KS, bias = S._prep(tf)
n = X["n"]
be = (1 + MA.FEE) / (1 + S.RR)
print("be=%.4f n=%d bias: long=%d short=%d none=%d" % (be, n,
      int((bias==1).sum()), int((bias==-1).sum()), int((bias==0).sum())))

def stats(rows):
    if not rows: return (0,0,0.0,float('nan'))
    nt = np.array([r["net"] for r in rows]); m=len(nt); w=int((nt>0).sum())
    net=(np.prod(1+nt)-1)*100
    return (m,w,net,S.bp(w,m,be))

def run(short_close, bias_arr=None, use_bias=True):
    rows = S.analyze_fast(tf, ab2=0.75, engulf='body', use_bias=use_bias,
                          short_close=short_close, body_frac=0.70, bias_arr=bias_arr)
    return rows

# --- reproduce primary + mirror
prim = run('high')
mir  = run('low')
m,w,net,p = stats(prim); print("PRIMARY  short<c1.high  n=%d win=%.1f%% net=%+.1f%% p=%.3f"%(m,100*w/m,net,p))
REAL_PRIM = net
m2,w2,net2,p2 = stats(mir); print("MIRROR   short<c1.low   n=%d win=%.1f%% net=%+.1f%% p=%.3f"%(m2,100*w2/m2,net2,p2))
REAL_MIR = net2

# year split primary
for yr in (2025,2026):
    for sd,lab in ((1,'LONG'),(-1,'SHORT')):
        rr=[r for r in prim if r['side']==sd and r['yr']==yr]
        m,w,net,p=stats(rr)
        if m: print("   %s %d n=%d win=%.1f%% net=%+.1f%% p=%.3f"%(lab,yr,m,100*w/m,net,p))

# =================== CIRCULAR-SHIFT NULL ===================
rng = np.random.default_rng(42)
K = 1000
offs = rng.integers(1, n-1, size=K)
prim_nets = np.empty(K); mir_nets = np.empty(K)
prim_wr = np.empty(K)
for k,off in enumerate(offs):
    ba = np.roll(bias, int(off))
    rp = run('high', bias_arr=ba)
    rm = run('low',  bias_arr=ba)
    mp,wp,np_,_ = stats(rp); prim_nets[k]=np_; prim_wr[k]= (100*wp/mp) if mp else 0
    mm_,wm,nm,_ = stats(rm); mir_nets[k]=nm

fp = float((prim_nets >= REAL_PRIM).mean())
fm = float((mir_nets  >= REAL_MIR ).mean())
fboth = float(((prim_nets>=REAL_PRIM)&(mir_nets>=REAL_MIR)).mean())
print("\n=== CIRCULAR-SHIFT NULL (K=%d) ==="%K)
print("real primary net=%+.2f%%  shift mean=%+.2f%% std=%.2f  frac>=real = %.4f"%(REAL_PRIM, prim_nets.mean(), prim_nets.std(), fp))
print("real mirror  net=%+.2f%%  shift mean=%+.2f%% std=%.2f  frac>=real = %.4f"%(REAL_MIR, mir_nets.mean(), mir_nets.std(), fm))
print("frac shifts beating BOTH real primary AND real mirror = %.4f"%fboth)
print("shift net pctiles: 90=%.2f 95=%.2f 99=%.2f max=%.2f"%(
    np.percentile(prim_nets,90),np.percentile(prim_nets,95),np.percentile(prim_nets,99),prim_nets.max()))
