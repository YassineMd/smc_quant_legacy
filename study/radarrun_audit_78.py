"""AUDIT items 7 & 8 for the LIVE portfolio (RR 15c+30c+30bkt), the config behind the 89.2%/91.5% headline win rates.
Item 7: avg WIN, avg LOSS, avg NET (expectancy) per trade at each TP in the sweep (0.20/0.25/0.30/0.35/0.40%).
Item 8: at TP 0.30%, haircut the win rate by 0/3/5/8 pp (flip random WINS to STOP-LOSSES at their own stop distance),
        report win%, expectancy/trade, prop pass%, median days, DDp99. Also prints the WORST historical trade (item 6).
Same detect/eval/mc as the shipped prop MC. python study/radarrun_audit_78.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_winrate_dd import sim
from study.radarrun_proptp_alltf_clock import detect, SLBUF, FEE, SLIP
from study.radarrun_hyro_prop import day_blocks, mc

SRCS = [("study/clock_archive", "15m"), ("study/clock_archive", "30m"), ("study/recon_archive", "30m")]
H = 200


def eval_carry(det, tp):
    """like eval_tp but carries (ts, net, r, dist, is_win). det = (sigs, Hi, Lo, C, n)."""
    sigs, Hi, Lo, C, n = det
    out = []; last = -1
    for (k, s, entry, sl, dist, ts) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        out.append([float(ts), net, net / dist, dist, net > 0]); last = k + int(off)
    return out


def pool(dets, tp):
    p = []
    for d in dets:
        p.extend(eval_carry(d, tp))
    p.sort(key=lambda t: t[0]); return p


def haircut(pooled, pp, seed):
    """flip `pp`% of ALL trades (chosen from winners) into stop-losses at their own dist. returns new [(ts,net,r)]."""
    rng = random.Random(seed)
    wins = [i for i, t in enumerate(pooled) if t[4]]
    k = int(round(pp / 100.0 * len(pooled)))
    k = min(k, len(wins))
    flip = set(rng.sample(wins, k))
    out = []
    for i, t in enumerate(pooled):
        ts, net, r, dist, isw = t
        if i in flip:
            net = -dist - FEE - 2 * SLIP            # this "win" actually hit its stop (non-TP exit -> extra slip)
            r = net / dist
        out.append((ts, net, r))
    return out


def prop(triples, Rp):
    days = day_blocks(triples)
    m = mc(days, Rp, 4.0, "R")
    return m


def main():
    dets = [detect(sorted(load_archive(tf, root=root, drop_degenerate=False)[1],
                          key=lambda b: _f(b.get("start_time", 0))), SLBUF.get(tf, 0.003)) for root, tf in SRCS]

    print("AUDIT 7 & 8 | LIVE = RR[15c+30c+30bkt] | fee %.4f RT + %.4f slip (+slip on non-TP) | H=%d\n" % (FEE, SLIP, H), flush=True)

    print("### ITEM 7 — avg win / avg loss / expectancy per trade, by TP (net of fees) ###", flush=True)
    print("  TP     n     win%   avgWin%   avgLoss%   expectancy/trade%", flush=True)
    for tp in (0.002, 0.0025, 0.003, 0.0035, 0.004):
        p = pool(dets, tp)
        nets = np.array([t[1] for t in p]) * 100.0
        w = nets[nets > 0]; l = nets[nets <= 0]
        print("  %.2f%%  %-5d %5.1f%%  %+.3f%%   %+.3f%%    %+.4f%%"
              % (tp * 100, len(p), 100.0 * (nets > 0).mean(), w.mean() if len(w) else 0.0,
                 l.mean() if len(l) else 0.0, nets.mean()), flush=True)

    p30 = pool(dets, 0.003)
    worst = min(p30, key=lambda t: t[1])
    print("\n### ITEM 6 (worst trade at TP0.30) ### worst single-trade net %+.3f%%  (R %+.2f, stop-dist %.3f%%)"
          % (worst[1] * 100, worst[2], worst[3] * 100), flush=True)

    for tp_hc in (0.0025, 0.003):
        phc = pool(dets, tp_hc)
        print("\n### ITEM 8 — win-rate haircut at TP %.2f%% (flip random wins -> stop-loss) ###" % (tp_hc * 100), flush=True)
        print("  haircut  win%   expectancy/trade%   R0.3: pass / med-d / DDp99      R0.4: pass / med-d / DDp99", flush=True)
        for pp in (0, 3, 5, 8):
            triples = haircut(phc, pp, seed=1000 + pp)
            nets = np.array([t[1] for t in triples]) * 100.0
            m3 = prop(triples, 0.3); m4 = prop(triples, 0.4)
            print("  -%dpp     %5.1f%%   %+.4f%%           %5.1f%% / %4.0f / %4.1f%%          %5.1f%% / %4.0f / %4.1f%%"
                  % (pp, 100.0 * (nets > 0).mean(), nets.mean(),
                     m3["p"], m3["d50"], m3["dd99"], m4["p"], m4["d50"], m4["dd99"]), flush=True)


if __name__ == "__main__":
    main()
