"""1h — do the swing VALUE-AREA EDGES (VAH / VAL) reverse price, trend-conditioned, vs a trend-matched placebo?

The classic reversion boundary is the EDGE of accepted value, not its interior. VAH = top of the swing's value area =
resistance (price rallies up to it, reverts DOWN); VAL = bottom = support (pulls down to it, reverts UP). Tested in
each edge's designated role and split by the 2-day trend (daily close dir AND daily-POC shift), placebo kept inside
the same trend+approach bucket. reversal = symmetric first-passage (+/-D). Recon 1h.

CLI: python study/swing_va_edges_1h.py
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.swing_va_reversal import load_tf
from study.swing_va_trend_1h import daily_trend, evaluate      # evaluate(A, specs, btr, D, bucket) w/ placebo
from app import swing_lvn_detect as SL


def build_edges(A):
    r = SL._dev_leg(A); H, L, C, thr, piv, dev = r
    vah = []; val = []
    for k in range(1, len(piv)):
        b0 = int(piv[k - 1][0]); b1 = int(piv[k][0]); cb = int(piv[k][3])
        if b1 <= b0:
            continue
        try:
            va = SL.va_lines(A, b0, b1)
        except Exception:
            va = None
        if not va:
            continue
        if va.get("vah") is not None:
            v = va["vah"]; vah.append((v * (1.0 - 1e-4), v * (1.0 + 1e-4), cb))
        if va.get("val") is not None:
            v = va["val"]; val.append((v * (1.0 - 1e-4), v * (1.0 + 1e-4), cb))
    return vah, val, thr


def row(name, res):
    verdict = "<-- BEATS placebo" if (res["edge"] >= 3 and res["p"] < 0.05) else ""
    print("  %-38s n=%-4d  reversal %5.1f%%  placebo %5.1f%%  edge %+5.1f pp  p=%.3f %s"
          % (name, res["n"], 100 * res["real"], 100 * res["plac"], res["edge"], res["p"], verdict))


def main():
    A = load_tf("1h")
    btr = daily_trend(A)
    vah, val, thr = build_edges(A)
    print("1h recon: %d candles | VAH levels=%d, VAL levels=%d | thr=%.2f%%" % (len(A), len(vah), len(val), thr * 100))
    for D in (0.01, 0.015):
        print("\n=== D = %.1f%% ===" % (D * 100))
        # VAL = SUPPORT (approach from ABOVE, fa=True); reversal = bounce UP
        row("VAL support  ALL (from above)", evaluate(A, val, btr, D, lambda tr, fa: fa))
        row("VAL support  UPtrend (aligned)", evaluate(A, val, btr, D, lambda tr, fa: fa and tr == 1))
        row("VAL support  DOWNtrend (counter)", evaluate(A, val, btr, D, lambda tr, fa: fa and tr == -1))
        # VAH = RESISTANCE (approach from BELOW, fa=False); reversal = reverse DOWN
        row("VAH resist   ALL (from below)", evaluate(A, vah, btr, D, lambda tr, fa: not fa))
        row("VAH resist   DOWNtrend (aligned)", evaluate(A, vah, btr, D, lambda tr, fa: (not fa) and tr == -1))
        row("VAH resist   UPtrend (counter)", evaluate(A, vah, btr, D, lambda tr, fa: (not fa) and tr == 1))


if __name__ == "__main__":
    main()
