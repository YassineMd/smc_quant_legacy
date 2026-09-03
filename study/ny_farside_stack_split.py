"""Does the EMA20/50 stack add an edge to the far-side HOLD candidate? (user 2026-09-03)

PRE-REGISTERED: same HOLD cells as study/ny_farside_strategy_1m (cp 0.25 / 0.30, disaster stop,
session-close exit). At each session's checkpoint take the 15m clock EMA20/50 state from the
LAST CLOSED 15m bar (causal). Split every HOLD trade by whether its direction AGREES with the
stack (long & E20>E50 / short & E20<E50) vs DISAGREES. All four splits reported, both eras.
PREDICTION ON RECORD: alignment adds nothing or hurts (18 conditioning families precedent).
python study/ny_farside_stack_split.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engulf_sr_detect import _ohlc
from study.archive_loader import load_archive
from study.ny_farside_strategy_1m import sessions, run_cell, report, S0, S1


def ema(vals, n):
    a = 2.0 / (n + 1.0)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = e + a * (v - e)
        out.append(e)
    return out


def stack_by_day(raws15, cp_off):
    """{utc_day: +1 if E20>E50 at the last CLOSED 15m bar before cp, -1 if below} (continuous EMAs)."""
    rows = []
    for b in raws15:
        st = int(float(b.get("start_time", 0)))
        _o, c, _h, _l = _ohlc(b)
        rows.append((st, c))
    rows.sort()
    C = [c for _t, c in rows]
    e20 = ema(C, 20)
    e50 = ema(C, 50)
    out = {}
    for k, (st, _c) in enumerate(rows):
        if k < 60:
            continue
        sod = st % 86400
        if sod + 900 <= cp_off:                     # bar CLOSES at/before the checkpoint
            out[st - sod] = 1 if e20[k] > e50[k] else -1
    return out


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    _b, raws1, _g = load_archive("1m", root=os.path.join(here, "clock_archive"), drop_degenerate=False)
    _b, raws15, _g = load_archive("15m", root=os.path.join(here, "clock_archive"), drop_degenerate=False)
    split_t = 1767225600.0
    sess_all = sessions(raws1)
    for label, sel in (("RECON 2025", lambda d: d < split_t), ("RECON 2026H1", lambda d: d >= split_t)):
        sess = {d: v for d, v in sess_all.items() if sel(d)}
        print("\n=== %s ===  NY sessions=%d" % (label, len(sess)))
        for f in (0.25, 0.30):
            cp_off = S0 + f * (S1 - S0)
            stk = stack_by_day([b for b in raws15 if sel(int(float(b.get("start_time", 0)))
                                                        - int(float(b.get("start_time", 0))) % 86400
                                                        or True)], cp_off)
            trades = run_cell(sess, f, "HOLD")
            agree = [t for t in trades if stk.get(t["day"]) == t["side"]]
            disagree = [t for t in trades if stk.get(t["day"]) == -t["side"]]
            report("cp%.2f/HOLD all" % f, trades)
            report("cp%.2f/HOLD stack-AGREE" % f, agree)
            report("cp%.2f/HOLD stack-AGAINST" % f, disagree)


if __name__ == "__main__":
    main()
