"""Does a high-vw% candle (strong directional bias) predict the NEXT candle's direction (continuation, same side)?

Per candle i: bias = up_ticks>dn_ticks (up) else down; vw% = (max(up,dn)/min - 1)*100 (the terminal's Ease 'vw');
aligned_r = rB (up bias) / rS (down bias) using the terminal formulas. OUTCOME = does candle i+1 have the SAME bias?
Reports P(same) vs the base rate, binned by vw%, split by direction, crossed with aligned_r, and a year split.
Exact two-sided binomial p vs base. Runs on recon (18mo) OR the live daemon archive (real ticks).

User's observation (15m): vw% > ~10% tends to predict the next candle is the same side.

CLI: python study/vw_continuation.py [tf] [recon|live]
"""
import gzip, json, glob, os, sys, math, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_tf(tf, root="recon_archive"):
    by_key = {}
    for fn in sorted(glob.glob(os.path.join(ROOT, "study", root, tf, "%s_*" % tf))):
        op = gzip.open if fn.endswith(".gz") else open
        with op(fn, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if isinstance(r, dict) and "data" in r:
                    d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                    k = int(r["bid"]) if "bid" in r else float(d.get("start_time", 0.0) or 0.0)
                else:
                    d = r; k = float(d.get("start_time", 0.0) or 0.0)
                by_key[k] = d
    return [by_key[k] for k in sorted(by_key)]


def flow(b):
    ut = float(b.get("up_ticks", 0.0) or 0.0); dtk = float(b.get("dn_ticks", 0.0) or 0.0)
    bv = float(b.get("buy_vol", 0.0) or 0.0); sv = float(b.get("sell_vol", 0.0) or 0.0)
    if bv <= 0 or sv <= 0 or ut <= 0 or dtk <= 0 or ut == dtk:
        return None
    vw = min(999.0, (max(ut, dtk) / min(ut, dtk) - 1.0) * 100.0)
    base = (ut + dtk) / (bv + sv)
    rB = (ut / bv) / base; rS = (dtk / sv) / base
    up = ut > dtk
    return vw, up, (rB if up else rS)                       # vw, up-bias?, aligned-side efficiency


def _lp(i, n, p):
    if p <= 0.0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if i == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p))


def binom_p(k, n, p):
    if n == 0 or p <= 0.0 or p >= 1.0:
        return 1.0
    lpk = _lp(k, n, p); tot = 0.0
    for i in range(n + 1):
        if _lp(i, n, p) <= lpk + 1e-9:
            tot += math.exp(_lp(i, n, p))
    return min(1.0, tot)


def line(rows, title, base=None):
    n = len(rows)
    if n == 0:
        print("  %-28s n=0" % title); return
    k = sum(1 for r in rows if r["same"]); rate = k / n
    extra = "  d=%+5.1fpp p=%.3f" % ((rate - base) * 100, binom_p(k, n, base)) if base is not None else ""
    print("  %-28s n=%-6d same-side=%.1f%%%s" % (title, n, 100 * rate, extra))
    return rate


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    src = sys.argv[2] if len(sys.argv) > 2 else "recon"
    root = "archive_data" if src == "live" else "recon_archive"
    buck = load_tf(tf, root); n = len(buck)
    fl = [flow(b) for b in buck]
    rows = []
    for i in range(n - 1):
        a, b = fl[i], fl[i + 1]
        if a is None or b is None:
            continue
        rows.append(dict(vw=a[0], up=a[1], ar=a[2], same=(a[1] == b[1]),
                         year=dt.datetime.utcfromtimestamp(float(buck[i].get("start_time", 0.0) or 0.0)).year))
    print("%s src=%s  |  %d buckets, %d usable candle-pairs %s..%s" % (
        tf, src, n, len(rows),
        dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date()), flush=True)
    if len(rows) < 100:
        print("  too few pairs (%d)" % len(rows)); return
    base = sum(1 for r in rows if r["same"]) / len(rows)
    print("  BASE P(next candle SAME side) = %.1f%%   (n=%d)" % (100 * base, len(rows)))
    print("\n  --- DISJOINT vw%% bands ---")
    for lo, hi in [(0, 3), (3, 6), (6, 10), (10, 15), (15, 25), (25, 50), (50, 1e9)]:
        lab = "vw [%g,%s)" % (lo, "%g" % hi if hi < 1e8 else "inf")
        line([r for r in rows if lo <= r["vw"] < hi], lab, base)
    print("\n  --- the user's cut ---")
    line([r for r in rows if r["vw"] > 10], "vw > 10", base)
    line([r for r in rows if r["vw"] <= 10], "vw <= 10", base)
    print("\n  --- vw>10 by bias direction ---")
    line([r for r in rows if r["vw"] > 10 and r["up"]], "vw>10 & UP-bias -> next up?", base)
    line([r for r in rows if r["vw"] > 10 and not r["up"]], "vw>10 & DOWN-bias -> next dn?", base)
    print("\n  --- vw>10 crossed with aligned-side efficiency (rB up / rS down) ---")
    line([r for r in rows if r["vw"] > 10 and r["ar"] >= 1.1], "vw>10 & aligned_r>=1.1", base)
    line([r for r in rows if r["vw"] > 10 and r["ar"] < 1.1], "vw>10 & aligned_r<1.1", base)
    print("\n  --- durability of vw>10 (by year) ---")
    for y in sorted({r["year"] for r in rows}):
        line([r for r in rows if r["vw"] > 10 and r["year"] == y], "vw>10 · %d" % y, base)


if __name__ == "__main__":
    main()
