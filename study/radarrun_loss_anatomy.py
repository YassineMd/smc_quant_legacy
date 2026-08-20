"""Anatomy of the RadarRun LOSERS (net-0.2% config: gross TP 0.27%, candle stop, pooled 15c+30c+30bkt). Where do the
~9% losses concentrate — by UTC hour, by session, by side, by source? Base win rate is the reference; a bucket only
'matters' if its loss rate is elevated AND it has enough n. ALL IN-SAMPLE / DESCRIPTIVE. python study/radarrun_loss_anatomy.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
FEE, SLIP, TP, H = 0.0004, 0.0003, 0.0027, 200
SRCS = [("study/clock_archive", "15m", "15c"), ("study/clock_archive", "30m", "30c"), ("study/recon_archive", "30m", "30bkt")]


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0), len(ph)


def session(h):
    if 0 <= h < 7:
        return "Asia   00-07"
    if 7 <= h < 13:
        return "London 07-13"
    if 13 <= h < 21:
        return "NY     13-21"
    return "Late   21-24"


def build():
    rows = []
    for root, tf, src in SRCS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        n = len(A); Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
        C = np.array([_f(b.get("close", b.get("close_price"))) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
        last = -1
        for (k, s, entry, csl, dist, ts) in detect(A, SLBUF.get(tf, 0.003))[0]:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            outc, gross, off = sim(s, entry, entry * (1 + s * TP), csl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
            net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
            dt = datetime.fromtimestamp(float(ST[k]), tz=timezone.utc)
            rows.append((net > 0, "long" if s > 0 else "short", src, dt.hour, session(dt.hour))); last = k + int(off)
    return rows


def tbl(rows, keyfn, label):
    from collections import defaultdict
    g = defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r[0])
    base = 100.0 * np.mean([r[0] for r in rows])
    print("  %-14s   n     win%%   loss%%   #losers   vs base(%.1f%%)" % (label, base), flush=True)
    for kk in sorted(g):
        v = g[kk]; wr = 100.0 * np.mean(v); nn = len(v); nl = sum(1 for x in v if not x)
        flag = "  <== ELEVATED" if (100 - wr) >= (100 - base) * 1.4 and nl >= 20 else ""
        print("  %-14s %-5d %5.1f%%  %5.1f%%   %-5d   %+.1fpp%s" % (str(kk), nn, wr, 100 - wr, nl, wr - base, flag), flush=True)
    print("", flush=True)


def main():
    rows = build()
    n = len(rows); base = 100.0 * np.mean([r[0] for r in rows]); nl = sum(1 for r in rows if not r[0])
    print("RadarRun LOSS ANATOMY | net-0.2%% (gross TP0.27%%, candle stop) | pooled 15c+30c+30bkt | n=%d  win %.1f%%  losers=%d | IN-SAMPLE\n"
          % (n, base, nl), flush=True)
    tbl(rows, lambda r: r[4], "SESSION")
    tbl(rows, lambda r: r[1], "SIDE")
    tbl(rows, lambda r: "%02d" % r[3], "UTC HOUR")
    print("  SESSION x SIDE (win%% | #losers):", flush=True)
    from collections import defaultdict
    g = defaultdict(list)
    for r in rows:
        g[(r[4], r[1])].append(r[0])
    for sess in ("Asia   00-07", "London 07-13", "NY     13-21", "Late   21-24"):
        parts = []
        for side in ("long", "short"):
            v = g.get((sess, side), [])
            if v:
                parts.append("%s %.1f%% (n=%d, L=%d)" % (side, 100 * np.mean(v), len(v), sum(1 for x in v if not x)))
        print("    %-14s  %s" % (sess, "   ".join(parts)), flush=True)
    print("\n  SOURCE x SIDE (win%%):", flush=True)
    g2 = defaultdict(list)
    for r in rows:
        g2[(r[2], r[1])].append(r[0])
    for src in ("15c", "30c", "30bkt"):
        parts = []
        for side in ("long", "short"):
            v = g2.get((src, side), [])
            if v:
                parts.append("%s %.1f%% (n=%d)" % (side, 100 * np.mean(v), len(v)))
        print("    %-6s  %s" % (src, "   ".join(parts)), flush=True)


if __name__ == "__main__":
    main()
