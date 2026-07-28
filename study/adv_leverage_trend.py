import os, sys; sys.path.insert(0, os.getcwd())
import numpy as np
import study.absorb_engulf_bias_15m as S
import study.mom_absorb_1h as MA

TF = "15m"
S.precompute(TF)
X, _ = S._PRE[TF]
BE = (1 + MA.FEE) / (1 + S.RR)
print("BE win =", round(BE, 4), " FEE=", MA.FEE, " RR=", S.RR)

def stats(rows):
    m = len(rows)
    if m == 0: return (0,0,0.0,float('nan'))
    nt = np.array([r["net"] for r in rows]); w = int((nt>0).sum())
    tot = (np.prod(1+nt)-1)*100
    return (m, w, tot, S.bp(w, m, BE))

def show(lab, rows):
    m,w,tot,p = stats(rows)
    wr = 100*w/m if m else 0
    print("  %-26s n=%3d win %5.1f%% net %+7.1f%% p=%.3f" % (lab, m, wr, tot, p))

def cell(rows, side=None, yr=None):
    out=[]
    for r in rows:
        if side is not None and r["side"]!=side: continue
        if yr is not None and r["yr"]!=yr: continue
        out.append(r)
    return out

# ---------- PRIMARY ----------
print("\n===== PRIMARY (bias ON, body, ab2=0.75, short_close=high, bf=0.70) =====")
prim = S.analyze_fast(TF, ab2=0.75, engulf="body", use_bias=True, short_close="high", body_frac=0.70)
show("ALL", prim)
show("LONG", cell(prim, side=1))
show("SHORT", cell(prim, side=-1))
for yr in (2025,2026):
    show("LONG %d"%yr, cell(prim, side=1, yr=yr))
    show("SHORT %d"%yr, cell(prim, side=-1, yr=yr))

# ---------- (1) LEVERAGE: drop regime-aligned cells ----------
print("\n===== (1) LEVERAGE — remove the two winning year-cells =====")
def drop(rows, *conds):
    # conds: list of (side,yr) tuples to remove
    return [r for r in rows if (r["side"], r["yr"]) not in conds]

show("full ALL", prim)
show("drop SHORT-2026", drop(prim, (-1,2026)))
show("drop LONG-2025",  drop(prim, (1,2025)))
show("drop BOTH",       drop(prim, (-1,2026), (1,2025)))
# also the reverse: keep ONLY the two winning cells
onlywin = [r for r in prim if (r["side"],r["yr"]) in ((-1,2026),(1,2025))]
show("ONLY the 2 win cells", onlywin)
rest = drop(prim, (-1,2026), (1,2025))
m,w,tot,_ = stats(onlywin); _,_,tot_full,_ = stats(prim)
print("   -> 2 win cells contribute net %+.1f%% of full %+.1f%%" % (tot, tot_full))

# ---------- (2) TREND CONTROL ----------
print("\n===== (2) TREND CONTROL — replace structure bias with SMA trend =====")
c = np.asarray(X["c"], float); n = len(c)
def sma_bias(N):
    b = np.zeros(n, int)
    csum = np.cumsum(np.insert(c,0,0.0))
    for i in range(n):
        if i < N:
            b[i]=0; continue
        sma = (csum[i+1]-csum[i+1-N])/N
        b[i] = 1 if c[i] > sma else (-1 if c[i] < sma else 0)
    return b

for N in (100,200):
    ba = sma_bias(N)
    tr = S.analyze_fast(TF, ab2=0.75, engulf="body", use_bias=True, short_close="high", body_frac=0.70, bias_arr=ba)
    print("--- SMA%d trend bias ---" % N)
    show("ALL", tr)
    show("LONG", cell(tr, side=1)); show("SHORT", cell(tr, side=-1))
    for yr in (2025,2026):
        show("LONG %d"%yr, cell(tr, side=1, yr=yr)); show("SHORT %d"%yr, cell(tr, side=-1, yr=yr))

# ---------- bias OFF baseline for reference ----------
print("\n===== bias OFF (reference) =====")
off = S.analyze_fast(TF, ab2=0.75, engulf="body", use_bias=False, short_close="high", body_frac=0.70)
show("ALL", off); show("LONG", cell(off,side=1)); show("SHORT", cell(off,side=-1))

# ---------- how much do structure bias and SMA overlap? ----------
print("\n===== structure vs SMA agreement on fired bars =====")
_, recs = S._PRE[TF]
strat_bias = {r["i"]: r["bias"] for r in recs}
for N in (100,200):
    ba = sma_bias(N)
    # among the OFF signals, what regime would each require, and how often do struct/sma agree at that bar
    agree=0; tot=0
    for r in off:
        pass
    # compare bias arrays over all bars where struct!=0
    m = np.array([strat_bias.get(i,0) for i in range(n)])
    both = (m!=0)&(ba!=0)
    ag = (m[both]==ba[both]).mean() if both.sum() else float('nan')
    print("  SMA%d agrees with structure on %.1f%% of bars where both nonzero (n=%d)"%(N, 100*ag, both.sum()))
