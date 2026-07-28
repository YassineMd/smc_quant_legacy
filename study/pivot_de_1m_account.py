"""PIVOT (frozen S5j-r5) D and E entries on the 18-month 1m reconstruction (study/recon_archive).

Two entry styles per fire, same frozen exit (TP +0.5% / SL -0.3%, 6h cap; taker 0.10% RT):
  D entry = MARKET at the DETECTION bar close (det_i) — no wait, so every fire trades.
  E entry = the frozen WAIT-baseline-touch entry (entry_i) — MKT/TOUCH; CANCELLED (no touch in 1h) is skipped.
Per-side sequential fire chain = exactly the terminal's selection (a buy gates the next buy, a sell the next sell).
Account: $200k, 10% margin x10 lev = 100% of balance notional/trade, compounded, single position (non-overlap by
exit bar). win = net>0 (TIME exits marked to market at the 6h cap). Memory-light: no per-bucket levels needed.

Run: python study/pivot_de_1m_account.py
"""
from __future__ import annotations
import os, sys, glob, gzip, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import pivot_detect as PD

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
H_S = 6 * 3600.0                 # 6h exit cap
FEE = 0.10                       # taker % round-trip (the pivot's frozen fee)
B0 = 200_000.0
MARGIN_FRAC = 0.10
LEVERAGE = 10.0
_KEEP = ("high", "low", "poc_price", "end_time", "start_time", "buy_vol", "sell_vol", "curr_vol",
         "buyer_er", "seller_er")


def load_1m_light():
    """Stream the 1m recon chunks, keeping ONLY the scalar fields the pivot needs (no levels) -> memory-safe."""
    files = sorted(glob.glob(os.path.join(RECON, "1m", "1m_*.jsonl.gz")),
                   key=lambda p: int(os.path.basename(p).split("_")[1]))
    out = []
    for fn in files:
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)["data"]
                b = {k: d.get(k, 0.0) for k in _KEEP}
                b["open"] = d.get("open_price", 0.0); b["close"] = d.get("close_price", 0.0)
                out.append(b)
    out.sort(key=lambda b: float(b["start_time"]))     # guarantee time order across chunks
    return out


def walk_fixed(eb, entry, side, hi, lo_, cl, et, st, n):
    """TP+0.5 / SL-0.3 from the entry close, adverse(SL)-first, 6h cap -> TIME exit at the cap bar close.
    Returns (outcome, net_pct, exit_bar)."""
    long = side == "long"
    sl = entry * (1 - 0.003) if long else entry * (1 + 0.003)
    tp = entry * (1 + 0.005) if long else entry * (1 - 0.005)
    t_e = float(et[eb]); j = eb + 1
    while j < n and st[j] <= t_e + H_S:
        adv = (lo_[j] <= sl) if long else (hi[j] >= sl)
        fav = (hi[j] >= tp) if long else (lo_[j] <= tp)
        if adv:
            return "SL", -0.3, j
        if fav:
            return "TP", 0.5, j
        j += 1
    xj = min(j, n - 1)                                   # 6h elapsed -> exit at that bar's close (mark to market)
    net = (cl[xj] - entry) / entry * 100.0 * (1.0 if long else -1.0)
    return "TIME", net, xj


def account(setups, hi, lo_, cl, et, st, n):
    """setups: list of (entry_bar, entry_px, side) sorted by entry_bar. Single position, non-overlap by exit bar."""
    bal = B0; last_exit = -1; nets = []; tp = sl = tm = 0
    for eb, epx, side in setups:
        if eb <= last_exit:
            continue                                     # already in a trade -> skip (100% notional)
        out, pct, xj = walk_fixed(eb, epx, side, hi, lo_, cl, et, st, n)
        last_exit = xj
        net = pct / 100.0 - FEE / 100.0
        nets.append(net)
        tp += out == "TP"; sl += out == "SL"; tm += out == "TIME"
        bal += (MARGIN_FRAC * bal * LEVERAGE) * net
        if bal <= 0:
            bal = 0.0; break
    return bal, nets, (tp, sl, tm)


def report(label, setups, hi, lo_, cl, et, st, n):
    if not setups:
        print("  %-22s n=0" % label); return
    bal, nets, (tp, sl, tm) = account(setups, hi, lo_, cl, et, st, n)
    nt = np.array(nets); k = len(nt)
    w = 100.0 * (nt > 0).sum() / k
    net = (np.prod(1 + nt) - 1) * 100
    print("  %-22s n=%4d  win %5.1f%% (tp %d / sl %d / time %d)  net %+6.1f%%   END $%9.0f   P&L $%+9.0f (%+.1f%%)"
          % (label, k, w, tp, sl, tm, net, bal, bal - B0, (bal - B0) / B0 * 100))


def main():
    print("loading recon 1m (light) ...", flush=True)
    snaps = load_1m_light()
    n = len(snaps)
    hi = np.array([b["high"] for b in snaps]); lo_ = np.array([b["low"] for b in snaps])
    cl = np.array([b["close"] for b in snaps]); et = np.array([b["end_time"] for b in snaps])
    st = np.array([float(b["start_time"]) for b in snaps])
    print("detecting pivots over %d 1m buckets ..." % n, flush=True)
    fires = PD.detect_pivots(snaps)

    # per-side sequential chain = the terminal's selection (buy gates next buy, sell gates next sell)
    fl = sorted(fires, key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; processed = []
    for f in fl:
        if f["det_i"] < scan[f["side"]]:
            continue
        processed.append(f)
        scan[f["side"]] = (f["entry_i"] + 1) if f["entry_i"] is not None else f["wait_end_i"]

    span = (st[-1] - st[0]) / 86400.0
    ncanc = sum(1 for f in processed if f["entry_i"] is None)
    print("=" * 104)
    print("PIVOT D & E entries on the 1m reconstruction  |  %d 1m buckets, %.0f days (%s -> %s)"
          % (n, span, dt.datetime.utcfromtimestamp(st[0]).strftime("%Y-%m-%d"),
             dt.datetime.utcfromtimestamp(st[-1]).strftime("%Y-%m-%d")))
    print("%d fires -> %d setups (%d cancelled E). Exit TP+0.5%%/SL-0.3%%/6h, fee %.2f%%. Account $%.0f @ 10%%x10 = 100%% notional."
          % (len(fires), len(processed), ncanc, FEE, B0))
    print("=" * 104)

    # D entries = market at det_i (every setup). E entries = entry_i (skip cancelled).
    D = [(f["det_i"], float(cl[f["det_i"]]), f["side"]) for f in processed]
    E = [(f["entry_i"], float(cl[f["entry_i"]]), f["side"]) for f in processed if f["entry_i"] is not None]

    print("D entries (market at detection):")
    report("D  ALL", sorted(D), hi, lo_, cl, et, st, n)
    report("D  LONG", sorted([x for x in D if x[2] == "long"]), hi, lo_, cl, et, st, n)
    report("D  SHORT", sorted([x for x in D if x[2] == "short"]), hi, lo_, cl, et, st, n)
    print("E entries (WAIT-baseline-touch):")
    report("E  ALL", sorted(E), hi, lo_, cl, et, st, n)
    report("E  LONG", sorted([x for x in E if x[2] == "long"]), hi, lo_, cl, et, st, n)
    report("E  SHORT", sorted([x for x in E if x[2] == "short"]), hi, lo_, cl, et, st, n)
    print("-" * 104)
    print("D = market at the detection bar; E = frozen WAIT-baseline-touch (cancelled skipped). Frozen exit, 0.10%% taker.")
    print("CAVEAT: reconstructed 1m buckets (independent bucketing, OI approximate, liquidations empty). Footprint fidelity lower at 1m.")


if __name__ == "__main__":
    main()
