"""PIVOT signal-exit backtest + SL sweep. Rule: NO fixed TP -- a position is held until the indicator prints
an OPPOSITE-side detection D (the flip), or the SL, whichever comes first. Same-side setups that fire while in
a position are IGNORED ('keep going'). Entries = the indicator's setup entries E; exit-signals = the opposite
side's setup DETECTIONS D (the badges the terminal shows). The SL sweep finds where the 'let it run to the
flip' logic stops being starved by too-tight a stop. Fee shown at 0.10% (baseline convention) and 0.20%
(realistic taker round-trip). Run: python study/pivot_signal_exit.py
"""
import os, sys, bisect, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict           # noqa: E402
from app import pivot_detect as PD                       # noqa: E402
from pivot_backtest import load_local_tape               # noqa: E402

SL_GRID = [0.002, 0.003, 0.004, 0.005, 0.006, 0.0075, 0.010, 0.0125, 0.015, 0.020]


def simulate(sl_frac, cl, hi, lo_, et, n, det_long, det_short, entries):
    """Play the signal-exit rule at one SL. Returns the list of taken trades (dicts)."""
    trades = []; last_exit = -1
    for E, side in entries:
        if E <= last_exit:                       # in a position when this entry fired -> ignore ('keep going')
            continue
        long = side == "long"; ep = float(cl[E])
        sl_lvl = ep * (1 - sl_frac) if long else ep * (1 + sl_frac)
        opp = det_short if long else det_long     # opposite-side detection bars
        i = bisect.bisect_right(opp, E); nd = opp[i] if i < len(opp) else None
        exit_bar = exit_px = reason = None
        for j in range(E + 1, (nd if nd is not None else n - 1) + 1):
            if (lo_[j] <= sl_lvl) if long else (hi[j] >= sl_lvl):   # SL intrabar wins ties on the signal bar
                exit_bar = j; exit_px = sl_lvl; reason = "SL"; break
        if exit_bar is None:
            if nd is None:
                break                             # position never flipped before the tape end -> stop
            exit_bar = nd; exit_px = float(cl[nd]); reason = "SIGNAL"
        raw = (exit_px - ep) / ep * 100.0 * (1.0 if long else -1.0)
        trades.append(dict(side=side, reason=reason, raw=raw, hold=(et[exit_bar] - et[E]) / 60.0))
        last_exit = exit_bar
    return trades


def agg(tr, fee):
    if not tr:
        return dict(n=0, net=float("nan"), tot=0.0, win=float("nan"), slp=float("nan"))
    raw = np.array([t["raw"] for t in tr]); net = raw - fee
    return dict(n=len(tr), net=net.mean(), tot=net.sum(), win=100.0 * np.mean(raw > 0),
                slp=100.0 * np.mean([t["reason"] == "SL" for t in tr]))


def main():
    t0 = time.time()
    bids, raws, gaps = load_local_tape(); n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    cl = np.array([b.close_price for b in bks]); hi = np.array([b.high for b in bks])
    lo_ = np.array([b.low for b in bks]); et = np.array([b.end_time for b in bks])
    snaps = [b.full_snapshot() for b in bks]
    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; setups = []
    for f in fires:
        if f["det_i"] < scan[f["side"]]:
            continue
        if f["entry_i"] is not None:
            setups.append(f); scan[f["side"]] = f["entry_i"] + 1
        else:
            scan[f["side"]] = f["wait_end_i"]
    det_long = sorted(s["det_i"] for s in setups if s["side"] == "long")
    det_short = sorted(s["det_i"] for s in setups if s["side"] == "short")
    entries = sorted((s["entry_i"], s["side"]) for s in setups)

    print("[%3.0fs] tape %d bars | %d setups | SL sweep of the signal-exit rule (TP = opposite-side D)"
          % (time.time() - t0, n, len(setups)))
    print("       columns: net/tr and TOTAL net are AFTER fee; SL%% = share of exits that hit the stop\n")
    hdr = ("  SL%   | ALL n  SLhit  win  | net/tr  totNET(.10)  totNET(.20) | "
           "long totNET(.10) | short totNET(.10)")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for slf in SL_GRID:
        tr = simulate(slf, cl, hi, lo_, et, n, det_long, det_short, entries)
        a10 = agg(tr, 0.10); a20 = agg(tr, 0.20)
        lg = agg([t for t in tr if t["side"] == "long"], 0.10)
        sh = agg([t for t in tr if t["side"] == "short"], 0.10)
        print("  %-5.2f | %3d   %4.0f%%  %4.1f%% | %+6.3f  %+8.2f    %+8.2f  | %+8.2f (n=%d)  | %+8.2f (n=%d)"
              % (slf * 100, a10["n"], a10["slp"], a10["win"], a10["net"], a10["tot"], a20["tot"],
                 lg["tot"], lg["n"], sh["tot"], sh["n"]))


if __name__ == "__main__":
    main()
