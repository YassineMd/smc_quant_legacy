"""Pivot V3 D & E entries vs the ADAPTIVE VPIN tier (causal).

For every V3 entry, look up the CAUSAL adaptive VPIN tier at the entry bar — the exact per-bucket tier the terminal
now displays (`vpin_adaptive.vpin_tiers_from_series`: each bucket judged against ONLY its trailing VPIN_ADAPT_WINDOW,
so it never repaints). Report outcome (net, RR-win, W/BE/L) by tier NORMAL / WARN / TOXIC, for the V3 D-entries
(cyan+step3) and E-entries (recorded combos), plus the broader all-fire pools for statistical power.

Finding (in-sample): the ABSOLUTE VPIN level does not separate winners from losers (corr ~0), but the ADAPTIVE tier
does — NORMAL-flow entries bleed, ELEVATED-flow (WARN or TOXIC = at/above the recent p75) entries are net-positive,
a ~+0.15%/trade gap consistent across D and E.

Run: python study/de_vpin.py
"""
import os, sys, pickle
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import de_zone_effectiveness as D                       # noqa: E402
from app import vpin_adaptive as VA, config             # noqa: E402

BE = D.BE


def _rr(r, mae, mfe):
    a, f = r[mae], r[mfe]
    if a is None:
        return False
    return (a >= -0.3 and f >= 2 * abs(a)) if r["buy"] else (f <= 0.3 and abs(a) >= 2 * f)


def _wbl(rows, nk):
    return (sum(1 for r in rows if r[nk] > BE), sum(1 for r in rows if abs(r[nk]) <= BE),
            sum(1 for r in rows if r[nk] < -BE))


def study(name, entries, tiers, n, nk, mae, mfe):
    entries = [(i, r) for i, r in entries if i is not None and 0 <= i < n]
    if not entries:
        print("%s: (empty)" % name); return
    print("#" * 90)
    print("%s   n=%d   net %+.3f%%/tr" % (name, len(entries), np.mean([r[nk] for _, r in entries])))
    print("   %-8s | n  | net/tr  | RR-win     | net W/BE/L" % "tier")
    for tl in (VA.NORMAL, VA.WARN, VA.TOXIC):
        g = [(i, r) for i, r in entries if tiers[i] == tl]
        if not g:
            print("   %-8s |  0 |" % tl); continue
        w = sum(1 for i, r in g if _rr(r, mae, mfe)); W, B, L = _wbl([r for _, r in g], nk)
        print("   %-8s | %2d | %+6.3f%% | %2d (%3.0f%%) | %d/%d/%d" % (
            tl, len(g), np.mean([r[nk] for _, r in g]), w, 100 * w / len(g), W, B, L))
    el = [r for i, r in entries if tiers[i] in (VA.WARN, VA.TOXIC)]
    no = [r for i, r in entries if tiers[i] == VA.NORMAL]
    print("   -> ELEVATED(warn+toxic) n=%d net%+.3f%% | NORMAL n=%d net%+.3f%%" % (
        len(el), np.mean([r[nk] for r in el]) if el else 0.0, len(no), np.mean([r[nk] for r in no]) if no else 0.0))


def main():
    recs = pickle.load(open(os.path.join(REPO, "study", "out", "de_zone_recs.pkl"), "rb"))
    raws, _ = D.load_1m_ids()
    vp = VA.rolling_vpin(raws, config.VPIN_WINDOW)
    tiers, _toxics = VA.vpin_tiers_from_series(vp)       # the exact causal tier the terminal displays
    n = len(tiers)
    study("V3 D-ENTRIES (cyan+step3)",
          [(r["det"], r) for r in recs if r["d_tier"] == "cyan" and r["d_step3"]], tiers, n, "d_net", "d_mae", "d_mfe")
    study("V3 E-ENTRIES (recorded combos)",
          [(r["e_det"], r) for r in recs if r.get("e_net") is not None and r["e_nonfaded"]], tiers, n, "e_net", "e_mae", "e_mfe")
    print("\n=== broader pools (statistical power) ===")
    study("all cyan D fires", [(r["det"], r) for r in recs if r["d_tier"] == "cyan"], tiers, n, "d_net", "d_mae", "d_mfe")
    study("ALL D fires", [(r["det"], r) for r in recs], tiers, n, "d_net", "d_mae", "d_mfe")
    study("ALL E fires", [(r["e_det"], r) for r in recs if r.get("e_net") is not None], tiers, n, "e_net", "e_mae", "e_mfe")


if __name__ == "__main__":
    main()
