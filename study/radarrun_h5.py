"""H5 — combined config. The only lever that moved per-trade CUSHION is H2 (time stop); H1 failed; H4 is a DD-tail
overlay (set DAILY_2LOSS below from the H4 result). Combined = live (TP0.25, candle stop) + time-stop N=2 [+ daily-2-loss
in the MC if H4 passed]. Report expectancy / BE / cushion + the full item-8 win-rate-haircut table (0/-3/-5/-8pp) for the
combined config vs the current LIVE config. Cushion is the number that matters (>6.9pp = thesis lives). ALL IN-SAMPLE =>
UPPER BOUND. python study/radarrun_h5.py [timeN] [daily2loss:0|1]"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF, FEE, SLIP
from study.radarrun_hyro_prop import day_blocks
from study.radarrun_h123 import resim
from study.radarrun_h4 import mc_h4

TIME_N = int(sys.argv[1]) if len(sys.argv) > 1 else 2
DAILY_2LOSS = (len(sys.argv) > 2 and sys.argv[2] == "1")
SRCS = [("study/clock_archive", "15m", "15c"), ("study/clock_archive", "30m", "30c"), ("study/recon_archive", "30m", "30bkt")]


def build(dets, time_n):
    r = []
    for det, src in dets:
        r.extend(resim(det, src, time_n=time_n))
    r.sort(key=lambda t: t[0]); return r


def stat(rows):
    nets = np.array([t[1] for t in rows]) * 100.0
    w = nets[nets > 0]; l = nets[nets <= 0]
    aw = w.mean() if len(w) else 0.0; al = l.mean() if len(l) else 0.0
    be = (-al) / (aw - al) * 100.0
    win = 100.0 * (nets > 0).mean()
    return win, aw, al, be, nets.mean(), win - be


def haircut_triples(rows, pp, seed):
    """flip pp%% of wins to losses; a flipped win draws a loss from THIS config's OWN realized loss distribution (so the
    combined config's time-stop-capped losses are represented faithfully, not replaced by full candle stops)."""
    rng = random.Random(seed)
    losses = [(t[1], t[1] / t[2]) for t in rows if not t[3]]   # (net, r) of this config's real losing trades
    wins = [i for i, t in enumerate(rows) if t[3]]             # t=(ts,net,eff,is_win,outc,src,side)
    k = min(int(round(pp / 100.0 * len(rows))), len(wins))
    flip = set(rng.sample(wins, k))
    out = []
    for i, t in enumerate(rows):
        ts, net, eff = t[0], t[1], t[2]
        if i in flip and losses:
            net, r = rng.choice(losses)               # this "win" was actually a loss in this config's own profile
        else:
            r = net / eff
        out.append((ts, net, r))
    return out


def haircut_table(name, rows, daily_2loss):
    print("  %s haircut (flip random wins -> stop):" % name, flush=True)
    print("    hc     win%   exp/tr%    R0.4: pass / med / DDp99", flush=True)
    ml = 2 if daily_2loss else None
    for pp in (0, 3, 5, 8):
        tr = haircut_triples(rows, pp, seed=2000 + pp)
        nets = np.array([t[1] for t in tr]) * 100.0
        m = mc_h4(day_blocks(tr), 0.4, ml)
        print("    -%dpp  %5.1f%%  %+.4f%%   %5.1f%% / %4.0f / %4.1f%%"
              % (pp, 100.0 * (nets > 0).mean(), nets.mean(), m["p"], m["med"], m["dd99"]), flush=True)


def main():
    dets = [(detect(sorted(load_archive(tf, root=root, drop_degenerate=False)[1],
                           key=lambda b: _f(b.get("start_time", 0))), SLBUF.get(tf, 0.003)), src) for root, tf, src in SRCS]
    live = build(dets, None)
    comb = build(dets, TIME_N)
    print("H5 — COMBINED (time-stop N=%d%s) vs LIVE | RR 15c+30c+30bkt TP0.25%% R0.4 | IN-SAMPLE UPPER BOUND\n"
          % (TIME_N, " + daily-2-loss" if DAILY_2LOSS else ""), flush=True)
    for tag, rows in (("LIVE (baseline)", live), ("COMBINED", comb)):
        w, aw, al, be, exp, cush = stat(rows)
        print("  %-16s win %.1f%%  avgWin %+.3f%%  avgLoss %+.3f%%  BE %.1f%%  exp %+.4f%%  CUSHION %.1fpp"
              % (tag, w, aw, al, be, exp, cush), flush=True)
    print("", flush=True)
    haircut_table("LIVE", live, daily_2loss=False)
    print("", flush=True)
    haircut_table("COMBINED", comb, DAILY_2LOSS)


if __name__ == "__main__":
    main()
