"""Adversarial: rebuild the market-structure BIAS array under 4 alternative reasonable
interpretations and re-run the PRIMARY config (body, ab2<=0.75, short_close<high).

Alternatives:
 (base)  as-shipped: mitigation OR 2-consecutive-same-formation
 (a)     MITIGATION-ONLY (ignore the 2-formation trigger)
 (b)     FORMATION-ONLY (ignore mitigations)
 (c)     3-consecutive same-kind formations (instead of 2)
 (d)     1-bar LAG on the shipped bias (bias[i-1])
"""
import os, sys
sys.path.insert(0, os.getcwd())
import numpy as np
import study.absorb_engulf_bias_15m as S
import study.mom_absorb_1h as MA

TF = "15m"
BE = (1 + MA.FEE) / (1 + S.RR)   # 0.4545


def build_events(X):
    Kp = S.E.K
    LEV = X["SUP"] + X["RES"]
    ev = []
    for x in LEV:
        ev.append((x["i0"] + Kp, 1, "form", x["kind"]))
        if x["i1"] is not None:
            ev.append((x["i1"], 0, "mit", x["kind"]))
    ev.sort(key=lambda e: (e[0], e[1]))
    return ev


def bias_from(X, use_mit=True, use_form=True, form_run=2):
    """Generic state machine mirroring S._prep, with switches."""
    n = X["n"]; ev = build_events(X)
    bias = np.zeros(n, int); state = 0; forms = []; ei = 0
    for i in range(n):
        while ei < len(ev) and ev[ei][0] <= i:
            _, _, typ, kind = ev[ei]
            if typ == "mit" and use_mit:
                state = 1 if kind == "R" else -1
            elif typ == "form" and use_form:
                forms.append(kind)
                if len(forms) >= form_run and all(f == forms[-1] for f in forms[-form_run:]):
                    state = 1 if forms[-1] == "S" else -1
            elif typ == "form":
                forms.append(kind)  # still track for run detection consistency (unused when use_form False)
            ei += 1
        bias[i] = state
    return bias


def rep(name, bias_arr):
    rows = S.analyze_fast(TF, ab2=0.75, engulf="body", use_bias=True,
                          short_close="high", body_frac=0.70, bias_arr=bias_arr)
    def agg(rs):
        m = len(rs)
        if m == 0:
            return "n=0"
        nt = np.array([r["net"] for r in rs]); w = int((nt > 0).sum())
        net = (np.prod(1 + nt) - 1) * 100
        return "n=%3d win %4.1f%% net %+6.1f%% p=%.3f" % (m, 100*w/m, net, S.bp(w, m, BE))
    L = [r for r in rows if r["side"] > 0]; Sh = [r for r in rows if r["side"] < 0]
    print("\n=== %s ===" % name)
    print("  ALL   ", agg(rows))
    print("  LONG  ", agg(L))
    print("  SHORT ", agg(Sh))
    for yy in (2025, 2026):
        print("   %d L" % yy, agg([r for r in L if r["yr"] == yy]),
              "| S", agg([r for r in Sh if r["yr"] == yy]))
    if bias_arr is not None:
        nb1 = int((bias_arr == 1).sum()); nbm = int((bias_arr == -1).sum()); nb0 = int((bias_arr == 0).sum())
        print("  regime bars: long %d / short %d / none %d" % (nb1, nbm, nb0))


def main():
    print("precompute...", flush=True)
    X, _ = S.precompute(TF)
    print("done. break-even win=%.1f%%" % (BE*100))

    # shipped baseline (bias_arr=None uses the cached shipped bias)
    rep("BASE (shipped: mit OR 2-form)", None)

    # (a) mitigation-only
    rep("(a) MITIGATION-ONLY", bias_from(X, use_mit=True, use_form=False))
    # (b) formation-only (2-consecutive)
    rep("(b) FORMATION-ONLY (2-run)", bias_from(X, use_mit=False, use_form=True, form_run=2))
    # (c) 3-consecutive same-kind formations (+ mitigation, matching base otherwise)
    rep("(c) 3-FORM-RUN (+mit)", bias_from(X, use_mit=True, use_form=True, form_run=3))
    # also pure 3-form-only for completeness
    rep("(c') 3-FORM-ONLY", bias_from(X, use_mit=False, use_form=True, form_run=3))
    # (d) 1-bar lag on shipped bias
    _, _, _, shipped = S._prep(TF)
    lag = np.zeros_like(shipped); lag[1:] = shipped[:-1]
    rep("(d) 1-BAR LAG (shipped[i-1])", lag)


if __name__ == "__main__":
    main()
