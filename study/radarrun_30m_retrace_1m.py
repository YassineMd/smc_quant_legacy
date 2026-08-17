"""HYPOTHESIS: a 30m Radar Runner mostly RETRACES (dips against you) before running to the 0.2% TP -- so a LIMIT entry
below the signal price gives a better fill and a bigger effective TP. Measured on the 1m path.

For every 30m breakout (entry = 30m bar close, candle-capped SL), walk the 1m buckets AFTER the bar closes:
  1) MARKET-entry baseline on 1m -> win/loss (finer intrabar ordering than 30m), and for WINNERS the MAE (max adverse
     excursion = deepest dip below entry, %) reached BEFORE the TP is touched. -> 'how often / how deep does it retrace'.
  2) LIMIT-entry at L below (long) / above (short): does the limit FILL before TP? If so, resolve SL/TP from the limit
     price (bigger TP distance 0.2%+L, smaller stop). Report fill-rate, win% + realized gain on fills, and EV PER SIGNAL
     (missed straight-to-TP winners count as 0) vs the market baseline -> 'is waiting for the dip worth it?'.
Conservative intrabar rule: adverse extreme assumed first (SL-before-TP; limit fills before TP in the fill bar).
DAEMON (honest, full 1m) first; RECON streamed (compact OHLC arrays, no dicts) under a guard. 3bps slip / 0.04% fee.
python study/radarrun_30m_retrace_1m.py"""
import os, sys, glob, gzip, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; SLBUF = 0.003; FEE = 0.0004; SLIP = 0.0003; TP = 0.002
CAP = 4000                                        # max 1m buckets to walk forward per signal
LS = (0.0005, 0.001, 0.0015, 0.002)               # limit offsets below/above the signal (0.05% .. 0.20%)
MAE_BINS = (0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003)


def sig30(A):
    """30m Radar Runner signals: (k, s, entry, sl, et) + arrays. et = breakout bar end_time (start walking 1m there)."""
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ET = np.array([_f(b.get("end_time", b.get("start_time"))) for b in A])
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += step - 1000
    out = []                                           # ALL raw breakouts (overlap-allowed): this is a per-signal PATH
    for (k, side) in sorted(ev):                       # study, so every signal is an independent retrace observation
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        if abs(entry - sl) / entry > 0:
            out.append((k, s, float(entry), float(sl), float(ET[k])))
    return out


def load_1m(root):
    """Stream 1m gz -> compact sorted (ST, Hi, Lo) float arrays. No dicts retained (recon 1m is 500M+)."""
    pat = os.path.join(root, "1m", "1m_*.jsonl.gz"); ST = []; HI = []; LO = []
    for fn in sorted(glob.glob(pat)):
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line); d = r["data"]
                if isinstance(d, str):
                    d = json.loads(d)
                ST.append(_f(d.get("start_time"))); HI.append(_f(d.get("high"))); LO.append(_f(d.get("low")))
    ST = np.array(ST); HI = np.array(HI); LO = np.array(LO)
    o = np.argsort(ST, kind="stable")
    return ST[o], HI[o], LO[o]


def market(H1, L1, i0, i1, s, entry, tp, sl):
    """Baseline on 1m. Returns (outcome 1/0/-1, mae_frac_before_tp)."""
    mae = 0.0
    for i in range(i0, i1):
        adv = (entry - L1[i]) / entry if s > 0 else (H1[i] - entry) / entry
        if adv > mae:
            mae = adv
        if (L1[i] <= sl) if s > 0 else (H1[i] >= sl):
            return 0, mae
        if (H1[i] >= tp) if s > 0 else (L1[i] <= tp):
            return 1, mae
    return -1, mae


def limit(H1, L1, i0, i1, s, entry, tp, sl, L):
    """Limit at L off the signal. Returns (state, net%): state in miss/nofill/win/loss/open."""
    lim = entry * (1 - L) if s > 0 else entry * (1 + L)
    fill = -1
    for i in range(i0, i1):
        if (L1[i] <= lim) if s > 0 else (H1[i] >= lim):          # adverse-first: limit fills before TP in-bar
            fill = i; break
        if (H1[i] >= tp) if s > 0 else (L1[i] <= tp):
            return "miss", 0.0                                   # ran to TP with no dip -> forgone winner
    if fill < 0:
        return "nofill", 0.0
    for i in range(fill, i1):
        if (L1[i] <= sl) if s > 0 else (H1[i] >= sl):
            g = s * (sl - lim) / lim; return "loss", g - FEE - 2 * SLIP
        if (H1[i] >= tp) if s > 0 else (L1[i] <= tp):
            g = s * (tp - lim) / lim; return "win", g - FEE - SLIP
    return "open", 0.0


def study(name, sigs, ST1, H1, L1):
    n1 = len(ST1); cov0, cov1 = float(ST1[0]), float(ST1[-1])
    base = []; maes = []                                          # baseline outcomes + winner MAEs
    lim_rows = {L: [] for L in LS}                                # per L: list of (state, net)
    used = 0; incov = 0
    for (k, s, entry, sl, et) in sigs:
        if et < cov0 or et > cov1:                               # signal outside 1m coverage -> can't study its path
            continue
        incov += 1
        i0 = int(np.searchsorted(ST1, et, side="left"))
        if i0 >= n1:
            continue
        i1 = min(n1, i0 + CAP)
        tp = entry * (1 + s * TP)
        o, mae = market(H1, L1, i0, i1, s, entry, tp, sl)
        if o == -1:
            continue                                             # never resolved in the 1m window -> drop
        used += 1
        net_m = (TP - FEE - SLIP) if o == 1 else (s * (sl - entry) / entry - FEE - 2 * SLIP)
        base.append((o, net_m))
        if o == 1:
            maes.append(mae)
        for L in LS:
            lim_rows[L].append(limit(H1, L1, i0, i1, s, entry, tp, sl, L))
    if used < 5:
        print("  %s: too few resolved signals (%d)" % (name, used)); return
    bo = np.array([b[0] for b in base]); bnet = np.array([b[1] for b in base])
    win = 100.0 * (bo == 1).mean()
    from datetime import datetime, timezone
    _d = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
    print("\n############  %s  (1m cov %s..%s; %d sigs in-cov, %d resolved)  ############" % (
        name, _d(cov0), _d(cov1), incov, used), flush=True)
    print("  MARKET entry @0.2%% TP on 1m path:  win=%.0f%%   EV/sig=%+.4f%%   (finer intrabar ordering than 30m)"
          % (win, 100 * bnet.mean()), flush=True)
    maes = np.array(maes)
    print("  RETRACE before TP (winners, n=%d):  median dip=%.3f%%   mean=%.3f%%   any dip>0.02%%: %.0f%%"
          % (len(maes), 100 * np.median(maes), 100 * maes.mean(), 100 * (maes > 0.0002).mean()), flush=True)
    print("  %% of winners that first dipped at least:", flush=True)
    for thr in MAE_BINS:
        print("     >= %.2f%% : %5.0f%% of winners" % (thr * 100, 100 * (maes >= thr).mean()), flush=True)
    print("  LIMIT entry (wait for the dip). fill=%% of ALL signals filled; EV/sig counts forgone winners as 0:", flush=True)
    print("     %-8s %6s %8s %10s %12s %10s" % ("offset", "fill%", "miss%", "win%|fill", "realizedTP%", "EV/sig%"), flush=True)
    print("     %-8s %6s %8s %10s %12s %10.4f   <-- baseline" % ("market", "100", "0", "%.0f" % win, "%.3f" % (100 * TP), 100 * bnet.mean()), flush=True)
    N = len(base)
    for L in LS:
        rows = lim_rows[L]
        fills = [r for r in rows if r[0] in ("win", "loss")]
        miss = sum(1 for r in rows if r[0] == "miss")
        wins = [r for r in fills if r[0] == "win"]
        evsig = sum(r[1] for r in fills) / N                      # forgone (miss/nofill/open) contribute 0
        wpf = (100.0 * len(wins) / len(fills)) if fills else 0.0
        realized = (100.0 * np.mean([r[1] for r in wins]) + 0.0) if wins else 0.0   # avg NET% on winning fills
        print("     %-8s %5.0f%% %7.0f%% %9.0f%% %11.3f %10.4f" % (
            "-%.2f%%" % (L * 100), 100.0 * len(fills) / N, 100.0 * miss / N, wpf, realized, 100 * evsig), flush=True)


def main():
    # DAEMON first (small 1m, the honest set) -> printed before any risky recon load.
    try:
        sd = sig30(get_buckets("30m", {}))
        ST1, H1, L1 = load_1m(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "archive_data"))
        study("DAEMON", sd, ST1, H1, L1)
        del ST1, H1, L1
    except Exception as e:
        print("  DAEMON failed: %s" % e, flush=True)
    try:
        sr = sig30(get_buckets("30m", {"root": "study/recon_archive"}))
        ST1, H1, L1 = load_1m("study/recon_archive")
        study("RECON", sr, ST1, H1, L1)
    except Exception as e:
        print("  RECON failed/skipped: %s" % e, flush=True)


if __name__ == "__main__":
    main()
