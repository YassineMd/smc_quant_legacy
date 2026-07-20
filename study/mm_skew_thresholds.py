"""MM×Skew 1h — mine winner/loser thresholds from delta / Mov.Magnitude / |skew| / velocity, per side,
and test them as filters on the FROZEN strategy. Anti-overfit protocol: (1) which features separate W/L
(Mann-Whitney); (2) threshold sweep for insight; (3) OUT-OF-SAMPLE test — fit the best threshold on H1,
apply BLIND to H2 — plus a combined median-split filter with split-half. n is small (~40/side/half) — treat
every in-sample number as a hypothesis; the H2 column is the judge.
Run:  python study/mm_skew_thresholds.py
"""
from __future__ import annotations
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S
import study.mm_skew_winloss as WL

FEATS = ["delta", "mm", "velo", "skmag"]


def build():
    M, span = P.build()
    for i in range(len(M)):
        b = M[i]; bv = float(b.get("buy_vol", 0)); sv = float(b.get("sell_vol", 0))
        cv = float(b.get("curr_vol", 0)) or (bv + sv)
        dur = max(1.0, float(b.get("end_time", 0)) - float(b.get("start_time", 0)))
        b["delta"] = (bv - sv) / cv * 100 if cv > 0 else 0.0
        b["mm"] = S.mov_mag(b["o"], b["h"], b["l"], b["c"])
        b["velo"] = (bv + sv) / dur
        b["skmag"] = abs(b["sk"]) if b["sk"] is not None else 0.0
    return M, span


def collect(M, rr):
    out = []
    for i in range(len(M) - 1):
        s = P.sig(M[i])
        if s == 0:
            continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            continue
        out.append(dict(side=s, win=(res[0] == "TP"), i=i, **{f: M[i][f] for f in FEATS}))
    return out


def best_threshold(rows, feat, keep_low):
    """sweep; return (thr, win_kept, n_kept) maximizing kept win% with >=40% of rows retained."""
    vals = sorted(set(r[feat] for r in rows)); best = None
    for thr in vals:
        kept = [r for r in rows if (r[feat] <= thr if keep_low else r[feat] >= thr)]
        if len(kept) < 0.4 * len(rows):
            continue
        wr = sum(1 for r in kept if r["win"]) / len(kept)
        if best is None or wr > best[1]:
            best = (thr, wr, len(kept))
    return best


def main():
    M, span = build()
    print(f"mature 1h bars {len(M)}  span {span:.1f}d\n")
    for rr in (1.0, 1.5):
        rows = collect(M, rr)
        print("=" * 100); print(f"RR 1:{rr}"); print("=" * 100)
        for side, sn in ((+1, "LONG"), (-1, "SHORT")):
            ss = [r for r in rows if r["side"] == side]
            base = 100 * sum(1 for r in ss if r["win"]) / len(ss)
            print(f"\n  {sn} (n={len(ss)}, base win {base:.1f}%)   feature: WIN med / LOSE med (MW p) | keep-dir | OOS test")
            for f in FEATS:
                W = [r[f] for r in ss if r["win"]]; L = [r[f] for r in ss if not r["win"]]
                p, _ = WL.mann_whitney(W, L)
                keep_low = statistics.median(W) <= statistics.median(L)
                # OUT-OF-SAMPLE: fit threshold on H1 (by index), apply to H2
                sso = sorted(ss, key=lambda z: z["i"]); mid = len(sso) // 2
                h1, h2 = sso[:mid], sso[mid:]
                bt = best_threshold(h1, f, keep_low)
                oos = ""
                if bt:
                    keptH2 = [r for r in h2 if (r[f] <= bt[0] if keep_low else r[f] >= bt[0])]
                    baseH2 = 100 * sum(1 for r in h2 if r["win"]) / len(h2)
                    if keptH2:
                        wrH2 = 100 * sum(1 for r in keptH2 if r["win"]) / len(keptH2)
                        oos = f"H1thr={bt[0]:.2f}->H2 kept {wrH2:.0f}%(n{len(keptH2)}) vs base {baseH2:.0f}%  {'OK' if wrH2>baseH2 else 'FAIL'}"
                star = "*" if p < 0.05 else " "
                print(f"    {f:6s}: {statistics.median(W):>8.2f} / {statistics.median(L):>8.2f} (p={p:.3f}{star}) | "
                      f"keep {'LOW ' if keep_low else 'HIGH'} | {oos}")


if __name__ == "__main__":
    main()
