"""RADAR RUNNER 30m — LIST THE LOSERS (for eyeballing). Uses the terminal's EXACT badge spec: app.radar_breakout_detect
.detect(sl_buf=0.003, tp_frac=config.RR_TP_FRAC=0.25%), entry=breakout-bar close, candle-anchored SL capped at the radar
extreme. Each fired signal simulated forward (SL-priority first-touch, conservative). A LOSER = SL hit before TP (or a
timeout that closed negative). Emits every losing badge chronologically with the fire time (= breakout bar's END time =
when the badge appears) in UTC and Moroccan local (UTC+1). Two substrates: 30m CLOCK (clock_archive) + 30m BUCKET
(recon_archive, volume-paced). python study/radarrun_30m_losers.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import config, radar_breakout_detect as RB

H, FEE, SLIP, SLBUF = 200, 0.0004, 0.0003, 0.003
TP_FRAC = config.RR_TP_FRAC
LOCAL = timezone(timedelta(hours=1))          # Moroccan (UTC+1; UTC+0 during Ramadan — eyeball offset)


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        if (pl[off] <= sl) if s > 0 else (ph[off] >= sl):     # SL checked first (conservative)
            return "SL", s * (sl - entry) / entry, off + 1
        if (ph[off] >= tp) if s > 0 else (pl[off] <= tp):
            return "TP", s * (tp - entry) / entry, off + 1
    return "END", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def run(root, tf, label):
    A = sorted(load_archive(tf, root=root)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A]); ET = np.array([_f(b.get("end_time")) for b in A])
    sigs = RB.detect(A, skip_last=True, sl_buf=SLBUF, tp_frac=TP_FRAC)
    rows = []
    for g in sigs:
        k = g["i"]; s = g["side"]; entry = g["entry"]; sl = g["sl_trade"]; tp = g["tp_trade"]
        dist = abs(entry - sl) / entry
        if dist <= 0 or k + 1 >= n:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "TP" else 0.0)
        rows.append(dict(k=k, s=s, entry=entry, sl=sl, tp=tp, outc=outc, off=off, net=net, R=net / dist,
                         st=ST[k], et=ET[k], y=datetime.fromtimestamp(ST[k], tz=timezone.utc).year))
    rows.sort(key=lambda r: r["st"])
    # headline: per-signal + non-overlap taken() win rate
    win_all = 100 * np.mean([1.0 if r["net"] > 0 else 0.0 for r in rows]) if rows else 0
    taken = []; last = -1
    for r in rows:
        if r["k"] > last:
            taken.append(r); last = r["k"] + int(r["off"])
    win_tk = 100 * np.mean([1.0 if r["net"] > 0 else 0.0 for r in taken]) if taken else 0
    losers = [r for r in rows if r["outc"] == "SL" or (r["outc"] == "END" and r["net"] < 0)]
    print("\n" + "=" * 100, flush=True)
    print("RADAR RUNNER 30m %s (%s)  |  entry=close, SL=candle+%.1f%%cap, TP=%.2f%%  |  fired signals=%d"
          % (label, tf, SLBUF * 100, TP_FRAC * 100, len(rows)), flush=True)
    print("  win-rate: per-badge=%.1f%%  non-overlap(taken n=%d)=%.1f%%   ->  LOSERS=%d (%.1f%% of badges)  [SL=%d, timeout-neg=%d]"
          % (win_all, len(taken), win_tk, len(losers), 100 * len(losers) / max(1, len(rows)),
             sum(1 for r in losers if r["outc"] == "SL"), sum(1 for r in losers if r["outc"] == "END")), flush=True)
    print("  by year:  " + "   ".join("%d: %d losers / %d badges" %
          (Y, sum(1 for r in losers if r["y"] == Y), sum(1 for r in rows if r["y"] == Y)) for Y in (2025, 2026)), flush=True)
    print("-" * 100, flush=True)
    print("  %-3s | %-16s | %-11s | %-5s | %-9s | %-9s | %-9s | %-4s | %-3s | %-7s"
          % ("#", "FIRE UTC (end)", "local+1", "side", "entry", "SL", "TP", "bars", "out", "R"), flush=True)
    for i, r in enumerate(losers, 1):
        fu = datetime.fromtimestamp(r["et"], tz=timezone.utc); fl = datetime.fromtimestamp(r["et"], tz=LOCAL)
        print("  %-3d | %-16s | %-11s | %-5s | %9.3f | %9.3f | %9.3f | %4d | %-3s | %+.2f"
              % (i, fu.strftime("%Y-%m-%d %H:%M"), fl.strftime("%m-%d %H:%M"),
                 "LONG" if r["s"] > 0 else "SHORT", r["entry"], r["sl"], r["tp"], r["off"], r["outc"], r["R"]), flush=True)
    return losers


def main():
    print("RADAR RUNNER 30m LOSERS — terminal badge spec (RR_TP_FRAC=%.2f%%, candle-SL 0.3%% cap, MINVISIT=%d)"
          % (TP_FRAC * 100, RB.MINVISIT), flush=True)
    run("study/clock_archive", "30m", "CLOCK")
    run("study/recon_archive", "30m", "BUCKET (volume-paced)")


if __name__ == "__main__":
    main()
