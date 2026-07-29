"""5m BREAKOUT strength: strong (follows through) vs weak (fails back). Label = ATR-symmetric first passage from the
break candle's close C: STRONG if price advances +1 ATR in the break direction before retreating -1 ATR, else WEAK
(ATR = trailing-20 median range, so the label is NOT confounded by the break candle's own size). Then compare the break
candle's OWN features (all known at its close -> genuinely predictive) + penetration + prev-day value-area position + OI,
strong vs weak, via AUC. Whole 5m recon. Directional features side-aligned (up-break +, down-break -).
"""
import os, sys, datetime as dt
import numpy as np
from scipy.stats import rankdata
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import support_resistance as SR
from app import absorption as ABS
from app.footprint_panel import profile_skewness
from app.engulf_sr_detect import _ohlc

K = SR.SR_PIVOT_K
ATR_W = 20
RR = 1.0
_, rows, _ = load_archive("5m", root=os.path.join("study", "recon_archive"))
A = sorted(rows, key=lambda b: float(b.get("start_time", 0) or 0)); n = len(A)
O = np.empty(n); C = np.empty(n); H = np.empty(n); Lo = np.empty(n); DAY = [None] * n
buy = np.empty(n); sell = np.empty(n)
for i, b in enumerate(A):
    o, c, h, l = _ohlc(b); O[i] = o; C[i] = c; H[i] = h; Lo[i] = l
    buy[i] = float(b.get("buy_vol", 0) or 0); sell[i] = float(b.get("sell_vol", 0) or 0)
    st = float(b.get("start_time", 0) or 0)
    if st > 0:
        DAY[i] = dt.datetime.utcfromtimestamp(st).date()
rng = H - Lo
ATR = np.zeros(n)                                        # trailing-20 median range (exclusive of the current bar)
for i in range(n):
    a = max(0, i - ATR_W)
    if i > a:
        ATR[i] = np.median(rng[a:i])
sys.stdout.write("loaded %d\n" % n); sys.stdout.flush()


def va_poc3(prof, pct=0.70):
    items = sorted((p, w) for p, w in (prof or {}).items() if w > 0)
    if len(items) < 3:
        return None
    total = sum(w for _, w in items); tgt = pct * total
    pi = max(range(len(items)), key=lambda k: items[k][1]); poc = items[pi][0]; lo = hi = pi; va = items[pi][1]
    while va < tgt and (lo > 0 or hi < len(items) - 1):
        up = items[hi + 1][1] if hi < len(items) - 1 else -1
        dn = items[lo - 1][1] if lo > 0 else -1
        if up >= dn: hi += 1; va += items[hi][1]
        else: lo -= 1; va += items[lo][1]
    return items[lo][0], items[hi][0], poc


def day_profiles():
    days = {}
    for b in A:
        st = float(b.get("start_time", 0) or 0)
        if st <= 0: continue
        d = dt.datetime.utcfromtimestamp(st).date(); prof = days.setdefault(d, {})
        for pr, v in (b.get("levels") or {}).items():
            try: p = float(pr)
            except (TypeError, ValueError): continue
            prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
    return days


YVA = {d: va_poc3(p) for d, p in day_profiles().items()}

levels = SR.detect(A, K, zone_mitigation=True)
brk = {}                                                 # i1 -> (kind, edge)  edge = the widened break boundary
for lv in levels:
    i1 = lv.get("i1")
    if i1 is not None:
        edge = lv["zhi"] if lv["kind"] == "R" else lv["zlo"]
        brk.setdefault(i1, (lv["kind"], edge))
events = [(i, k, e) for i, (k, e) in brk.items() if ATR_W <= i < n - 1 and ATR[i] > 0]
events.sort()
sys.stdout.write("breakouts: %d\n" % len(events)); sys.stdout.flush()

SIGNED0 = {"dir", "delta", "dvpin", "da2", "skew", "tick_imb", "oi_open_n", "A", "A_h2"}
SHARE5 = {"buy_share", "close_loc", "er_share"}


def cfeat(i):
    b = A[i]; o, c, h, l = O[i], C[i], H[i], Lo[i]; bv = buy[i]; sv = sell[i]; vol = bv + sv or 1e-9
    d = bv - sv; dh1 = float(b.get("delta_h1", 0) or 0); r = max(h - l, 1e-9)
    up = float(b.get("up_ticks", 0) or 0); dn = float(b.get("dn_ticks", 0) or 0)
    ber = float(b.get("buyer_er", 0) or 0); ser = float(b.get("seller_er", 0) or 0)
    oi = (float(b.get("opL", 0) or 0) + float(b.get("opS", 0) or 0)) - (float(b.get("clL", 0) or 0) + float(b.get("clS", 0) or 0))
    try: Aab = ABS.absorption(A, i)[0]
    except Exception: Aab = None
    try: Ah2 = ABS.absorption_halves(A, i)[1]
    except Exception: Ah2 = None
    return {"dir": 1.0 if c > o else (-1.0 if c < o else 0.0), "body_pct": abs(c - o) / o * 100,
            "range_pct": r / o * 100, "body_to_range": abs(c - o) / r, "upper_wick": (h - max(o, c)) / o * 100,
            "lower_wick": (min(o, c) - l) / o * 100, "close_loc": (c - l) / r, "vpin": abs(d) / vol, "dvpin": d / vol,
            "buy_share": bv / vol, "delta": d, "da2": (d - 2 * dh1) / vol, "A": Aab if Aab is not None else 0.0,
            "A_h2": Ah2 if Ah2 is not None else 0.0, "skew": profile_skewness(b.get("levels")) or 0.0,
            "oi_open_n": oi / vol, "er_share": ber / (ber + ser) if (ber + ser) > 0 else 0.5,
            "tick_imb": (up - dn) / (up + dn) if (up + dn) > 0 else 0.0, "vel_ratio": float(b.get("vel_ratio", 0) or 0)}


def aln(f, v, side):
    if f in SIGNED0: return v * side
    if f in SHARE5: return (v - 0.5) * side
    return v


def outcome(i, side):
    c = C[i]; atr = ATR[i]; tgt_up = c + RR * atr; tgt_dn = c - RR * atr
    for j in range(i + 1, n):
        if side > 0:
            adv = H[j] >= tgt_up; rev = Lo[j] <= tgt_dn
        else:
            adv = Lo[j] <= tgt_dn; rev = H[j] >= tgt_up
        if adv and rev: return 0            # same bar both -> conservative WEAK
        if adv: return 1
        if rev: return 0
    return None


def sfeat(i, side, edge):
    f = {}
    for role, j in (("brk", i), ("prev", i - 1)):
        for k, v in cfeat(j).items():
            f["%s_%s" % (role, k)] = aln(k, v, side)
    atr = ATR[i]
    f["pen_atr"] = (C[i] - edge) * side / atr             # how decisively it closed past the level, in ATR
    f["range_atr"] = (H[i] - Lo[i]) / atr
    yv = YVA.get(DAY[i] - dt.timedelta(days=1))
    if yv:
        val, vah, poc = yv; span = max(vah - val, 1e-9)
        pos = (C[i] - val) / span
        f["y_va_pos"] = pos if side > 0 else (1 - pos)   # side-aligned: >1 = breaking OUT beyond the favorable VA edge
        f["y_poc_aln"] = (C[i] - poc) / C[i] * 100 * side
        f["y_inside_va"] = 1.0 if val <= C[i] <= vah else 0.0
    else:
        f["y_va_pos"] = np.nan; f["y_poc_aln"] = np.nan; f["y_inside_va"] = np.nan
    f["hour_utc"] = float(dt.datetime.utcfromtimestamp(float(A[i].get("start_time", 0) or 0)).hour)
    return f


def auc(pos, neg):
    pos = np.array([x for x in pos if x == x], float); neg = np.array([x for x in neg if x == x], float)
    if len(pos) < 8 or len(neg) < 8: return None
    r = rankdata(np.concatenate([pos, neg]))
    u = r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2.0
    return u / (len(pos) * len(neg))


recs = []
for i, k, e in events:
    side = 1 if k == "R" else -1
    oc = outcome(i, side)
    if oc is None: continue
    recs.append((oc, k, sfeat(i, side, e)))
FKEYS = list(recs[0][2].keys())


def report(label, rr):
    strong = [r for r in rr if r[0] == 1]; weak = [r for r in rr if r[0] == 0]
    print("\n" + "=" * 92)
    print("%s | n=%d  STRONG %d (%.1f%%)  WEAK %d" % (label, len(rr), len(strong),
          100 * len(strong) / max(1, len(rr)), len(weak)))
    print("=" * 92)
    if len(strong) < 8 or len(weak) < 8:
        print("  (too few)"); return
    out = []
    for f in FKEYS:
        sv = [r[2][f] for r in strong]; wv = [r[2][f] for r in weak]; a = auc(sv, wv)
        if a is None: continue
        out.append((abs(a - 0.5), f, np.nanmean(sv), np.nanmean(wv), a))
    print("  %-16s %11s %11s %8s" % ("feature", "strong-mean", "weak-mean", "AUC"))
    for _, f, sm, wm, a in sorted(out, reverse=True):
        print("  %-16s %11.3f %11.3f %8.3f" % (f, sm, wm, a))


report("ALL BREAKOUTS (5m)", recs)
report("UP-breaks (resistance, R)", [r for r in recs if r[1] == "R"])
report("DOWN-breaks (support, S)", [r for r in recs if r[1] == "S"])
print("\nAUC>0.5 => feature higher in STRONG breaks. All features are on the BREAK candle / prev / prev-day VA -> known")
print("at the break's close, so a discriminator here is a genuine strong-vs-weak TELL. pen_atr = ATR-units the close")
print("cleared the level; y_va_pos side-aligned (>1 = breaking beyond the prev-day value area). Whole recon, per-break.")
