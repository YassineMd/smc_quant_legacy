"""Validate the CAUSAL app/reversal_detect against the descriptive ZigZag pivots, on recon 1h.

Reports: fire rate; precision (fires landing within +/-TOL of a same-type ZigZag pivot); recall (pivots caught by a
fire); and a forward-move sanity check (after a TOP fire, does price fall over the next K candles vs baseline; after a
BOTTOM fire, rise). The detector is causal; the pivots are look-ahead, so precision/recall just gauge how well the
live shape matches confirmed flips — not a tradeable-edge claim.

CLI: python study/reversal_detect_validate.py [WICK] [CLOSE] [RANGE_MULT]
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.candle_bias_1h import load
from app import reversal_detect as RD
from app import swing_lvn_detect as SL

TOL = 1          # a fire "hits" a pivot if within +/-TOL bars
K = 4            # forward candles for the reversal sanity check


def main():
    if len(sys.argv) > 1:
        RD.WICK = float(sys.argv[1])
    if len(sys.argv) > 2:
        RD.CLOSE = float(sys.argv[2])
    if len(sys.argv) > 3:
        RD.RANGE_MULT = float(sys.argv[3])
    A = load(); n = len(A)
    C = [b["close"] for b in A]
    print("recon 1h: %d candles | WICK=%.2f CLOSE=%.2f RANGE_MULT=%.2f EFF=%.0f"
          % (n, RD.WICK, RD.CLOSE, RD.RANGE_MULT, RD.EFF))

    fires = RD.detect(A, skip_last=True)
    tops_f = {e["i"] for e in fires if e["side"] < 0}
    bots_f = {e["i"] for e in fires if e["side"] > 0}
    strong = sum(1 for e in fires if e["strong"])
    print("fires: %d  (tops %d, bottoms %d; strong/gold %d)  = %.1f%% of candles"
          % (len(fires), len(tops_f), len(bots_f), strong, 100.0 * len(fires) / n))

    r = SL._dev_leg(A); H, L, Cc, thr, piv, dev = r
    ptops = {int(b) for (b, p, hi, cb) in piv if hi and 0 <= b < n}
    pbots = {int(b) for (b, p, hi, cb) in piv if (not hi) and 0 <= b < n}
    print("ZigZag(%.2f%%): pivots %d (tops %d, bottoms %d)" % (thr * 100, len(ptops) + len(pbots), len(ptops), len(pbots)))

    def near(i, pset):
        return any((i + d) in pset for d in range(-TOL, TOL + 1))

    def prec_rec(fset, pset, label):
        if not fset:
            print("  %-7s precision --   recall --   (no fires)" % label); return
        hits = sum(1 for i in fset if near(i, pset))
        caught = sum(1 for p in pset if near(p, fset))
        print("  %-7s precision %.1f%% (%d/%d fires near a pivot)   recall %.1f%% (%d/%d pivots caught)"
              % (label, 100.0 * hits / len(fset), hits, len(fset),
                 100.0 * caught / max(1, len(pset)), caught, len(pset)))
    prec_rec(tops_f, ptops, "TOP")
    prec_rec(bots_f, pbots, "BOTTOM")

    # forward-move sanity: mean forward return over K candles after a fire, vs the unconditional mean
    def fwd(i):
        j = min(n - 1, i + K)
        return (C[j] - C[i]) / C[i] * 100.0 if C[i] > 0 else 0.0
    uncond = sum(fwd(i) for i in range(RD.RANGE_WIN, n - K)) / max(1, (n - K - RD.RANGE_WIN))
    mt = (sum(fwd(i) for i in tops_f) / len(tops_f)) if tops_f else float("nan")
    mb = (sum(fwd(i) for i in bots_f) / len(bots_f)) if bots_f else float("nan")
    print("\nforward %d-candle mean move  (reversal => TOP negative, BOTTOM positive):" % K)
    print("  unconditional %+.3f%%" % uncond)
    print("  after TOP fire %+.3f%%   (want < unconditional)" % mt)
    print("  after BOTTOM fire %+.3f%%   (want > unconditional)" % mb)


if __name__ == "__main__":
    main()
