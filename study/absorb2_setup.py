"""absorb2 v2: A<=0 + EXCEPTION REVERSAL. 15 random days. CORE + continuation vs reversal split."""
import os,sys,datetime as dt,random
import numpy as np
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy"); sys.path.insert(0,os.getcwd())
from study.archive_loader import load_archive
from app import absorb2_detect as AB
from app.engulf_sr_detect import _ohlc
FEE=0.08
_,rows,_=load_archive("5m",root=os.path.join("study","recon_archive"))
A=sorted(rows,key=lambda b:float(b.get("start_time",0)or 0));n=len(A)
H=[0.]*n;L=[0.]*n;DAY=[None]*n
for i,b in enumerate(A):
    _,_,h,l=_ohlc(b);H[i]=h;L[i]=l
    st=float(b.get("start_time",0)or 0)
    if st>0: DAY[i]=dt.datetime.utcfromtimestamp(st).date()
random.seed(15); seldays=set(random.sample(sorted({d for d in DAY if d}),15))
def outc(s):
    i,side,tp,sl=s["i"],s["side"],s["tp"],s["sl"]
    for j in range(i+1,n):
        hs=(L[j]<=sl) if side>0 else (H[j]>=sl); ht=(H[j]>=tp) if side>0 else (L[j]<=tp)
        if hs and ht: return 0
        if ht: return 1
        if hs: return 0
    return None
sigs=[s for s in AB.detect(A) if DAY[s["i"]] in seldays]
def stat(lbl, sub):
    rs=[]
    for s in sub:
        o=outc(s)
        if o is None: continue
        risk=abs(s["entry"]-s["sl"])/s["entry"]*100
        rrx=(s["tp"]-s["entry"])/(s["entry"]-s["sl"]) if s["side"]>0 else (s["entry"]-s["tp"])/(s["sl"]-s["entry"])
        rs.append((s["side"],o,risk,rrx))
    if not rs: print("  %-26s n=0"%lbl); return
    w=[x for x in rs if x[1]==1]; l=[x for x in rs if x[1]==0]
    pf=(sum(x[2]*x[3] for x in w)/sum(x[2] for x in l)) if l and sum(x[2] for x in l)>0 else float("inf")
    net=np.mean([(x[3]*x[2] if x[1]==1 else -x[2])-FEE for x in rs])
    print("  %-26s n=%d win %.1f%% PF %.2f net %+.3f%%/tr"%(lbl,len(rs),100*len(w)/len(rs),pf,net))
print("=== absorb2 v2 (A<=0 + reversals) | 15 random days ===")
print("ALL:")
stat("all",sigs); stat("  LONG",[s for s in sigs if s["side"]>0]); stat("  SHORT",[s for s in sigs if s["side"]<0])
print("CONTINUATION (losange, rev=False):")
c=[s for s in sigs if not s.get("rev")]; stat("cont",c); stat("  LONG",[s for s in c if s["side"]>0]); stat("  SHORT",[s for s in c if s["side"]<0])
print("REVERSAL (triangle, rev=True):")
r=[s for s in sigs if s.get("rev")]; stat("rev",r); stat("  LONG",[s for s in r if s["side"]>0]); stat("  SHORT",[s for s in r if s["side"]<0])
print("BE ~47%% at 1:1.5 after fee. In-sample, 15 days.")
