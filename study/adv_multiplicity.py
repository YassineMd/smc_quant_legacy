import os, sys; sys.path.insert(0, os.getcwd())
import numpy as np
import study.absorb_engulf_bias_15m as S
import study.mom_absorb_1h as MA

tf="15m"; S.precompute(tf)
be=(1+MA.FEE)/(1+S.RR)

def stats(rows):
    if not rows: return (0,0,0.0,float('nan'))
    nt=np.array([r["net"] for r in rows]); m=len(nt); w=int((nt>0).sum())
    net=(np.prod(1+nt)-1)*100
    return (m,w,net,S.bp(w,m,be))

# ---- the year-robust search grid ----
engulfs=['body','range']; scloses=['high','low']; ab2s=[0.75,-0.75,0.5]; bfracs=[0.60,0.70,0.80]
configs=[(e,sc,a,b) for e in engulfs for sc in scloses for a in ab2s for b in bfracs]
print("configs=%d"%len(configs))

cells=[]  # (label, m, w, net, p)  disjoint year x side cells
for (e,sc,a,b) in configs:
    rows=S.analyze_fast(tf, ab2=a, engulf=e, use_bias=True, short_close=sc, body_frac=b)
    for yr in (2025,2026):
        for sd,sl in ((1,'L'),(-1,'S')):
            rr=[r for r in rows if r['side']==sd and r['yr']==yr]
            m,w,net,p=stats(rr)
            if m>=15:
                cells.append(("%s/%s/a%.2f/b%.2f %s%d"%(e,sc,a,b,sl,yr),m,w,net,p))

ncells=len(cells)
nsig=sum(1 for c in cells if c[4]<0.05)
nsig_pos=sum(1 for c in cells if c[4]<0.05 and c[3]>0)
exp=0.05*ncells
print("\nYEAR x SIDE cells with n>=15: %d"%ncells)
print("expected p<0.05 by chance ~ %.1f  |  actually p<0.05: %d  (of which net>0: %d)"%(exp,nsig,nsig_pos))
print("\ncells clearing p<0.05:")
for c in sorted(cells,key=lambda x:x[4]):
    if c[4]<0.05:
        print("  %-32s n=%3d win=%.1f%% net=%+.1f%% p=%.3f"%(c[0],c[1],100*c[2]/c[1],c[3],c[4]))

# year-robust requirement: a config/side net-positive above fee-BE in BOTH 2025 AND 2026 with n>=15/yr
print("\n--- YEAR-ROBUST check: same config+side net>0 & win>be in BOTH years, n>=15/yr ---")
robust=[]
for (e,sc,a,b) in configs:
    rows=S.analyze_fast(tf, ab2=a, engulf=e, use_bias=True, short_close=sc, body_frac=b)
    for sd,sl in ((1,'L'),(-1,'S')):
        ok=True; det=[]
        for yr in (2025,2026):
            rr=[r for r in rows if r['side']==sd and r['yr']==yr]
            m,w,net,p=stats(rr)
            det.append((yr,m,w,net))
            if not (m>=15 and net>0 and (w/m if m else 0)>be):
                ok=False
        if ok:
            robust.append(("%s/%s/a%.2f/b%.2f %s"%(e,sc,a,b,sl),det))
print("year-robust configs found: %d"%len(robust))
for lab,det in robust:
    print("  %-30s %s"%(lab, " ".join("%d:n%d w%d net%+.1f"%(y,m,w,nt) for (y,m,w,nt) in det)))
