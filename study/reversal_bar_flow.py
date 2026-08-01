"""Reversal-bar flow at swing pivots: what vw% / rB / rS on the bar that ENDS a swing predicts a strong counter-swing.

For each confirmed ZigZag pivot piv[k] (end of the swing piv[k-1]->piv[k], start of the counter-swing piv[k]->piv[k+1]),
the REVERSAL BAR = the pivot bar itself (bp = piv[k].bar). Reversal direction = OPPOSITE the swing (down after an up-swing,
up after a down-swing). Per-bar flow uses the SAME formulas the terminal's footprint 'Ease' row shows:
    vw%  = (max(up_ticks, dn_ticks) / min - 1) * 100     (directional conviction, clamp 999)
    base = (up_ticks + dn_ticks) / (buy_vol + sell_vol)
    rB   = (up_ticks / buy_vol) / base     rS = (dn_ticks / sell_vol) / base
reversal_vw = +vw when the bar LEANS in the reversal direction (dn>up at a high pivot / up>dn at a low pivot) — a genuine
              counter bar — else -vw (a momentum top/bottom that ran out of steam).
reversal_r  = rS at a high pivot (sellers taking over) / rB at a low pivot (buyers taking over).
OUTCOME     = the counter-swing magnitude |dP| of piv[k]->piv[k+1] (%), and whether it EXCEEDS the prior swing |dP| of
              piv[k-1]->piv[k] (a full reversal, not just a stall).

Reports the reversal-bar distributions + counter-swing size / full-reversal rate binned by reversal_vw and reversal_r,
plus a 2025/2026 durability split for the best band. Runs on the recon (18mo) OR the live daemon archive (real ticks).

CLI: python study/reversal_bar_flow.py [tf] [thr_pct] [recon|live] [bar_offset]   (offset 0=pivot bar, 1=confirmation)
"""
import gzip, json, glob, os, sys, math, statistics as st, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import structure as S


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


def _pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs); k = (len(s) - 1) * p / 100.0; f = int(k)
    return s[f] if f + 1 >= len(s) else s[f] + (s[f + 1] - s[f]) * (k - f)


def bar_flow(b):
    """(vw, up_lean, rB, rS) for one bucket using the terminal's footprint formulas, or None if degenerate."""
    ut = float(b.get("up_ticks", 0.0) or 0.0); dtk = float(b.get("dn_ticks", 0.0) or 0.0)
    bv = float(b.get("buy_vol", 0.0) or 0.0); sv = float(b.get("sell_vol", 0.0) or 0.0)
    if bv <= 0 or sv <= 0 or (ut + dtk) <= 0:
        return None
    vw = min(999.0, (max(ut, dtk) / min(ut, dtk) - 1.0) * 100.0) if min(ut, dtk) > 0 else 999.0
    base = (ut + dtk) / (bv + sv)
    rB = (ut / bv) / base if (ut > 0 and base > 0) else 0.0
    rS = (dtk / sv) / base if (dtk > 0 and base > 0) else 0.0
    return vw, (ut > dtk), rB, rS


def rep(sub, title, base_cont=None):
    if not sub:
        print("  %-30s n=0" % title); return
    cs = [r["counter"] for r in sub]
    full = sum(1 for r in sub if r["full"]) / len(sub)
    extra = "  d_full=%+.1fpp" % ((full - base_cont) * 100) if base_cont is not None else ""
    print("  %-30s n=%-5d counter|dP| med=%.2f%% mean=%.2f%%   full-rev=%.0f%%%s" % (
        title, len(sub), st.median(cs), st.mean(cs), 100 * full, extra))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    thr = (float(sys.argv[2]) if len(sys.argv) > 2 else 2.0) / 100.0
    src = sys.argv[3] if len(sys.argv) > 3 else "recon"
    off = int(sys.argv[4]) if len(sys.argv) > 4 else 0     # reversal bar = pivot bar (0) or the confirmation bar (1)
    root = "archive_data" if src == "live" else "recon_archive"
    buck = load_tf(tf, root); n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    piv = S._zigzag_confirmed(H, L, thr)
    print("%s thr=%.2f%% src=%s bar=bp+%d  |  %d buckets %d pivots %s..%s" % (
        tf, thr * 100, src, off, n, len(piv),
        dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date()), flush=True)

    rows = []
    for k in range(1, len(piv) - 1):                       # need prior (k-1) and next (k+1) pivots
        bp, pp, eh = piv[k][0], piv[k][1], piv[k][2]       # pivot bar / price / is_high
        br = bp + off                                      # the REVERSAL bar we measure
        if not (0 <= br < n) or br >= piv[k + 1][0]:       # keep it inside the counter-swing
            continue
        fl = bar_flow(buck[br])
        if fl is None:
            continue
        vw, up_lean, rB, rS = fl
        # reversal direction = opposite the swing. eh (swing HIGH) -> reversal DOWN; low pivot -> reversal UP.
        lean_rev = (not up_lean) if eh else up_lean        # bar leans toward the reversal direction?
        reversal_vw = vw if lean_rev else -vw
        reversal_r = rS if eh else rB                      # efficiency of the side that must take over
        counter = abs((piv[k + 1][1] - pp) / pp * 100.0) if pp > 0 else 0.0
        prior = abs((pp - piv[k - 1][1]) / piv[k - 1][1] * 100.0) if piv[k - 1][1] > 0 else 0.0
        rows.append(dict(reversal_vw=reversal_vw, reversal_r=reversal_r, lean_rev=lean_rev, eh=eh,
                         counter=counter, prior=prior, full=(counter > prior),
                         year=dt.datetime.utcfromtimestamp(float(buck[br]["start_time"])).year))

    if len(rows) < 50:
        print("  too few pivots (%d)" % len(rows)); return
    base_full = sum(1 for r in rows if r["full"]) / len(rows)
    rep(rows, "ALL reversal bars")
    print("  reversal-bar leans toward the reversal: %.0f%% (a genuine counter bar) / %.0f%% momentum" % (
        100 * sum(1 for r in rows if r["lean_rev"]) / len(rows),
        100 * sum(1 for r in rows if not r["lean_rev"]) / len(rows)))
    print("  reversal_vw  med=%.0f [p25..p75 %.0f..%.0f]   reversal_r med=%.2f [%.2f..%.2f]" % (
        st.median([r["reversal_vw"] for r in rows]), _pct([r["reversal_vw"] for r in rows], 25),
        _pct([r["reversal_vw"] for r in rows], 75),
        st.median([r["reversal_r"] for r in rows]), _pct([r["reversal_r"] for r in rows], 25),
        _pct([r["reversal_r"] for r in rows], 75)))
    print("\n  --- DISJOINT bands of reversal_vw (+ = counter-lean bar, - = momentum top/bottom) ---")
    for lo, hi in [(-1e9, -50), (-50, 0), (0, 30), (30, 80), (80, 200), (200, 1e9)]:
        lab = "reversal_vw [%s,%s)" % ("%.0f" % lo if lo > -1e8 else "-inf", "%.0f" % hi if hi < 1e8 else "inf")
        rep([r for r in rows if lo <= r["reversal_vw"] < hi], lab, base_full)
    print("\n  --- DISJOINT bands of reversal_r (efficiency of the taking-over side: rS at highs / rB at lows) ---")
    for lo, hi in [(-1e9, 0.6), (0.6, 0.9), (0.9, 1.1), (1.1, 1.5), (1.5, 2.5), (2.5, 1e9)]:
        lab = "reversal_r [%.1f,%s)" % (lo if lo > -1e8 else 0.0, "%.1f" % hi if hi < 1e8 else "inf")
        rep([r for r in rows if lo <= r["reversal_r"] < hi], lab, base_full)
    print("\n  --- counter-lean bars only, crossed vw x r ---")
    cl = [r for r in rows if r["lean_rev"]]
    rep([r for r in cl if r["reversal_vw"] >= 30 and r["reversal_r"] >= 1.1], "counter-lean & vw>=30 & r>=1.1", base_full)
    rep([r for r in cl if r["reversal_vw"] >= 30 and r["reversal_r"] < 1.1], "counter-lean & vw>=30 & r<1.1", base_full)
    rep([r for r in cl if r["reversal_vw"] < 30], "counter-lean & vw<30", base_full)
    print("\n  --- momentum tops/bottoms (bar leaned WITH the old swing) ---")
    rep([r for r in rows if not r["lean_rev"]], "momentum (anti-reversal lean)", base_full)
    print("\n  --- durability of counter-lean & vw>=30 & r>=1.1 (2025 vs 2026) ---")
    for y in (2025, 2026):
        rep([r for r in cl if r["reversal_vw"] >= 30 and r["reversal_r"] >= 1.1 and r["year"] == y], "· %d" % y, base_full)


if __name__ == "__main__":
    main()
