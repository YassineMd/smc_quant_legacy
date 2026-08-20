"""AUDIT item 3 — intrabar path. For the LIVE portfolio at TP 0.30%: (a) same-bar tie policy in the shipped sim,
(b) % of trades that resolve on the FIRST bar after entry, (c) % of trades whose RESOLVING bar contained BOTH the TP and
the stop (the genuinely ambiguous ones), (d) win rate under STOP-first (shipped default) vs TP-first (optimistic).
python study/radarrun_audit_3.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF, FEE, SLIP
H = 200
SRCS = [("study/clock_archive", "15m"), ("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def sim_info(s, entry, tp, sl, ph, pl, pc, tp_first):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        hit_sl = (lo <= sl) if s > 0 else (hi >= sl)
        hit_tp = (hi >= tp) if s > 0 else (lo <= tp)
        both = hit_sl and hit_tp
        if both:
            if tp_first:
                return "tp", s * (tp - entry) / entry, off + 1, True
            return "sl", s * (sl - entry) / entry, off + 1, True
        if tp_first:
            if hit_tp:
                return "tp", s * (tp - entry) / entry, off + 1, False
            if hit_sl:
                return "sl", s * (sl - entry) / entry, off + 1, False
        else:
            if hit_sl:
                return "sl", s * (sl - entry) / entry, off + 1, False
            if hit_tp:
                return "tp", s * (tp - entry) / entry, off + 1, False
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph), False


def evalp(dets, tp, tp_first):
    rows = []
    for (sigs, Hi, Lo, C, n) in dets:
        last = -1
        for (k, s, entry, sl, dist, ts) in sigs:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            outc, gross, off, both = sim_info(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1], tp_first)
            net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
            rows.append((net, off, both, outc)); last = k + int(off)
    return rows


def main():
    dets = [detect(sorted(load_archive(tf, root=root, drop_degenerate=False)[1],
                          key=lambda b: _f(b.get("start_time", 0))), SLBUF.get(tf, 0.003)) for root, tf in SRCS]
    tp = 0.003
    base = evalp(dets, tp, tp_first=False)     # shipped default = STOP-first
    opt = evalp(dets, tp, tp_first=True)       # optimistic = TP-first
    n = len(base)
    off1 = sum(1 for r in base if r[1] == 1)
    bothbar = sum(1 for r in base if r[2])
    win_stopfirst = 100.0 * np.mean([r[0] > 0 for r in base])
    win_tpfirst = 100.0 * np.mean([r[0] > 0 for r in opt])
    print("AUDIT item 3 — intrabar (LIVE 15c+30c+30bkt, TP 0.30%%), n=%d\n" % n, flush=True)
    print("  same-bar tie policy in shipped sim(): STOP checked before TP -> STOP wins same-bar ties (already pessimistic)", flush=True)
    print("  trades resolving on the FIRST bar after entry (off==1): %d  (%.1f%%)" % (off1, 100.0 * off1 / n), flush=True)
    print("  trades whose RESOLVING bar contained BOTH tp and stop (ambiguous): %d  (%.1f%%)" % (bothbar, 100.0 * bothbar / n), flush=True)
    print("  win rate  STOP-first (shipped default): %.1f%%" % win_stopfirst, flush=True)
    print("  win rate  TP-first (optimistic):        %.1f%%" % win_tpfirst, flush=True)
    print("  => same-bar assumption swings win rate by %.1f pp" % (win_tpfirst - win_stopfirst), flush=True)


if __name__ == "__main__":
    main()
