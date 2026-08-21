"""CONFIRM the user's hypothesis: during London, price tends to REVERSE between 10am-12 MOROCCAN (= 09:00-11:00 UTC).
Two independent confirmations, weekdays, London window 08:00-13:00 UTC (09:00-14:00 Moroccan), 5m clock:
  (1) SESSION-EXTREME TIMING — when does the London high / low (the turning point) form? A reversal point IS a session
      extreme, so if reversals cluster at 10am-12 Moroccan, the highs+lows cluster in 09:00-11:00 UTC. Baseline: uniform
      over the 5h session -> the 2h window would hold ~40%.
  (2) REVERSAL RATE by split-time T — pre = London-open(08:00 UTC)->T move; post = T->London-close(13:00 UTC) move.
      reversal = sign(pre) != sign(post), among days with a non-trivial pre-move (|pre|>=0.3%). >50% = reverses more than
      chance; the T with the max reversal rate = the pinpoint. Labels in Moroccan time (UTC+1). IS(2025)/OOS(2026).
python study/london_reversal_confirm.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
LON0, LON1 = 8, 13            # London window UTC hours [08:00, 13:00)
MOR = 1                       # Moroccan offset (UTC+1, current/non-Ramadan)
PRE_MIN = 0.003              # min pre-move (0.3%) to count a day in the reversal-rate test
ROOT, TF = "study/clock_archive", "5m"


def load():
    A = sorted(load_archive(TF, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); MN = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc)
        HR[i] = dt.hour; MN[i] = dt.minute; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, HR, MN, DATE, WD, n


def bin30(hr, mn):
    return hr * 2 + (1 if mn >= 30 else 0)                        # 30-min bin index


def mlabel(utc_hr, half):
    h = utc_hr + MOR; return "%02d:%02d" % (h, 30 if half else 0)  # Moroccan time label


def collect(D):
    O, C, Hi, Lo, ST, HR, MN, DATE, WD, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    days = []
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        lon = [i for i in sorted(idxs) if LON0 <= HR[i] < LON1]
        if len(lon) < 40:
            continue
        yr = datetime.fromtimestamp(ST[lon[0]], tz=timezone.utc).year
        hi_i = max(lon, key=lambda i: Hi[i]); lo_i = min(lon, key=lambda i: Lo[i])
        o8 = O[lon[0]]; endp = C[lon[-1]]
        # price at each 30-min mark (open of the bar at that mark)
        marks = {}
        for i in lon:
            k = (HR[i], MN[i])
            if k[1] in (0, 30) and k not in marks:
                marks[k] = O[i]
        days.append(dict(yr=yr, o8=o8, endp=endp, tH=(HR[hi_i], MN[hi_i]), tL=(HR[lo_i], MN[lo_i]), marks=marks))
    return days


def main():
    D = load(); days = collect(D)
    print("LONDON REVERSAL — confirm 10am-12 Moroccan (= 09:00-11:00 UTC) | London 08-13 UTC | n=%d weekdays | 5m clock\n" % len(days), flush=True)

    # (1) session-extreme timing
    print("== (1) WHEN does the London high / low form? (30-min bins, Moroccan time) ==", flush=True)
    print("  bin(Mor) UTC   %%high  %%low   %%either", flush=True)
    inwin_either = {None: 0, 2025: 0, 2026: 0}; tot = {None: 0, 2025: 0, 2026: 0}
    for uh in range(LON0, LON1):
        for half in (0, 1):
            b = uh * 2 + half
            nh = sum(1 for x in days if bin30(*x["tH"]) == b); nl = sum(1 for x in days if bin30(*x["tL"]) == b)
            print("  %s   %02dUTC  %4.1f%%  %4.1f%%  %4.1f%%" % (mlabel(uh, half), uh + (0 if not half else 0),
                  100 * nh / len(days), 100 * nl / len(days), 100 * (nh + nl) / (2 * len(days))), flush=True)
    for x in days:
        for lab in (None, x["yr"]):
            tot[lab] += 1
            if 9 <= x["tH"][0] < 11:
                inwin_either[lab] += 0.5
            if 9 <= x["tL"][0] < 11:
                inwin_either[lab] += 0.5
    print("\n  -> extremes forming in 09:00-11:00 UTC (10am-12 Moroccan): ALL %.1f%%  IS %.1f%%  OOS %.1f%%  (uniform baseline = 40%%)"
          % (100 * inwin_either[None] / tot[None], 100 * inwin_either[2025] / tot[2025], 100 * inwin_either[2026] / tot[2026]), flush=True)

    # (2) reversal rate by split time T
    print("\n== (2) REVERSAL RATE by split-time T: pre=open(09:00 Mor)->T, post=T->London close (14:00 Mor) ==", flush=True)
    print("  (reversal = post reverses pre, among days with |pre|>=0.3%%; >50%% = reverses more than a coin flip)", flush=True)
    print("  T(Mor)  UTC    n    revRate   IS      OOS    avg|pre| avg reversal-of-pre", flush=True)
    for uh in range(9, 12):
        for half in (0, 1):
            k = (uh, 30 if half else 0)
            rows = [x for x in days if k in x["marks"]]
            def rr(subset):
                cnt = 0; rev = 0; premags = []; retr = []
                for x in subset:
                    pre = (x["marks"][k] - x["o8"]) / x["o8"]; post = (x["endp"] - x["marks"][k]) / x["marks"][k]
                    if abs(pre) < PRE_MIN:
                        continue
                    cnt += 1; premags.append(abs(pre) * 100)
                    if (pre > 0) != (post > 0):
                        rev += 1
                    retr.append(-np.sign(pre) * post * 100)      # how much of pre got reversed (in %)
                return cnt, (100 * rev / cnt if cnt else 0), (np.mean(premags) if premags else 0), (np.mean(retr) if retr else 0)
            cA, rA, pm, rv = rr(rows); _, rI, _, _ = rr([x for x in rows if x["yr"] == 2025]); _, rO, _, _ = rr([x for x in rows if x["yr"] == 2026])
            print("  %s   %02dUTC  %3d   %5.1f%%   %5.1f%%  %5.1f%%   %.2f%%   %+.3f%%" % (mlabel(uh, half), uh, cA, rA, rI, rO, pm, rv), flush=True)


if __name__ == "__main__":
    main()
