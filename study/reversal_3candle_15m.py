"""What determines a 15m reversal over the LAST 3 CANDLES (not a single candle)?

The single-candle detector misses swing bottoms/tops that form as a SEQUENCE (sell into the low -> absorb ->
turn up) rather than one wide-range rejection bar. This studies the 3-candle window [i-2, i-1, i] (detection point
= i, fully CAUSAL): compare windows that CONTAIN a swing pivot (a ZigZag low/high at i-2, i-1 or i) vs ordinary
windows, over 3-candle features. AUC = P(feature higher at a reversal window). TOP/BOTTOM separate, 2025/26 split.

CLI: python study/reversal_3candle_15m.py
"""
import os, sys, math
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import swing_lvn_detect as SL

NAN = float("nan")


def mean(xs):
    xs = [x for x in xs if x == x]
    return (sum(xs) / len(xs)) if xs else NAN


def main():
    _, rows, _ = load_archive("15m", root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    for b in A:
        b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
    n = len(A)
    O = [b["open"] for b in A]; C = [b["close"] for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    ST = [_f(b.get("start_time")) for b in A]
    YR = [datetime.fromtimestamp(s, tz=timezone.utc).year for s in ST]
    DP = [NAN] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); bv = _f(A[i].get("buy_vol")); sv = _f(A[i].get("sell_vol"))
        if cv > 0:
            DP[i] = (bv - sv) / cv * 100.0

    NAMES = ("w_range", "recovery", "net", "lowpos", "highpos", "nbull", "last_bull",
             "lwick3", "uwick3", "delta3", "delta_flip", "delta_at_ext", "vol_climax")
    S = {k: [NAN] * n for k in NAMES}
    VOL = [_f(A[i].get("buy_vol")) + _f(A[i].get("sell_vol")) for i in range(n)]
    for i in range(3, n - 1):
        a, b, c = i - 2, i - 1, i
        Hs = [H[a], H[b], H[c]]; Ls = [L[a], L[b], L[c]]
        maxH = max(Hs); minL = min(Ls); rng = maxH - minL
        if O[a] <= 0 or rng <= 0:
            continue
        S["w_range"][i] = rng / O[a] * 100.0
        S["recovery"][i] = (C[c] - minL) / rng                 # close position in the 3-candle range (bottom=high, top=low)
        S["net"][i] = (C[c] - O[a]) / O[a] * 100.0
        S["lowpos"][i] = float(Ls.index(minL))                 # 0=oldest .. 2=current
        S["highpos"][i] = float(Hs.index(maxH))
        S["nbull"][i] = float(sum(1 for k in (a, b, c) if C[k] > O[k]))
        S["last_bull"][i] = 1.0 if C[c] > O[c] else 0.0
        body_lo = min(min(O[k], C[k]) for k in (a, b, c)); body_hi = max(max(O[k], C[k]) for k in (a, b, c))
        S["lwick3"][i] = (body_lo - minL) / rng
        S["uwick3"][i] = (maxH - body_hi) / rng
        d3 = [DP[k] for k in (a, b, c) if DP[k] == DP[k]]
        if d3:
            S["delta3"][i] = sum(d3)
        if DP[a] == DP[a] and DP[c] == DP[c]:
            S["delta_flip"][i] = DP[c] - DP[a]                 # flow shift over the window (bottom: sell->buy = positive)
        ext_k = (a, b, c)[Ls.index(minL)]                      # delta at the LOW candle (bottom orientation)
        if DP[ext_k] == DP[ext_k]:
            S["delta_at_ext"][i] = DP[ext_k]
        base = mean([VOL[k] for k in range(max(0, i - 20), i)])
        if base and base > 0:
            S["vol_climax"][i] = (VOL[a] + VOL[b] + VOL[c]) / 3.0 / base

    _, _, _, thr, piv, _ = SL._dev_leg(A)
    bot_det, top_det, pivbars = set(), set(), set()
    for (bar, pr, ih, cb) in piv:
        p = int(bar); pivbars.add(p)
        for i in (p, p + 1, p + 2):                            # detection points whose last-3 window contains this pivot
            if 0 <= i < n:
                (top_det if ih else bot_det).add(i)
    excl = set()
    for p in pivbars:
        excl.update(range(p - 2, p + 4))
    nonpiv = [i for i in range(3, n - 1) if i not in excl]
    print("recon 15m: %d  ZigZag thr=%.2f%%  pivots=%d  bottom-windows=%d top-windows=%d nonpiv=%d"
          % (n, thr * 100, len(pivbars), len(bot_det), len(top_det), len(nonpiv)))

    def tab(det, tag):
        rows_ = []
        for st in NAMES:
            pv = [S[st][i] for i in det if S[st][i] == S[st][i]]
            nv = [S[st][i] for i in nonpiv if S[st][i] == S[st][i]]
            if len(pv) < 30 or len(nv) < 100:
                continue
            auc, p, _, _ = auc_p(pv, nv)
            a25 = auc_p([S[st][i] for i in det if YR[i] == 2025 and S[st][i] == S[st][i]],
                        [S[st][i] for i in nonpiv if YR[i] == 2025 and S[st][i] == S[st][i]])[0]
            a26 = auc_p([S[st][i] for i in det if YR[i] == 2026 and S[st][i] == S[st][i]],
                        [S[st][i] for i in nonpiv if YR[i] == 2026 and S[st][i] == S[st][i]])[0]
            rows_.append((abs(auc - 0.5), st, auc, p, mean(pv), mean(nv), a25, a26))
        rows_.sort(reverse=True)
        print("\n=== %s : 3-candle window vs ordinary  (AUC>0.5 => higher at reversal) ===" % tag)
        print("%-13s  %6s %8s  %10s %10s  %5s/%5s" % ("feature", "AUC", "p", "mean@rev", "mean@ord", "25", "26"))
        for _, st, auc, p, mp, mo, a25, a26 in rows_:
            star = " *" if (abs(auc - 0.5) >= 0.05 and p < 0.01 and (a25 - 0.5) * (a26 - 0.5) > 0) else ""
            print("%-13s  %6.3f %8.4f  %10.4f %10.4f  %5.2f/%5.2f%s" % (st, auc, p, mp, mo, a25, a26, star))

    tab(bot_det, "BOTTOM window (swing LOW in last 3)")
    tab(top_det, "TOP window (swing HIGH in last 3)")


if __name__ == "__main__":
    main()
