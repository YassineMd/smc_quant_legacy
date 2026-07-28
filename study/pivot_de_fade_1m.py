"""PIVOT V3 D & E entries — FADED vs NON-FADED — on the 18-month 1m reconstruction, with the terminal's own exit.

Uses the CANONICAL frozen V3 rule (study/pivot_v3_de_zone_pdf.build_records): per fire it computes the D entry
(d_net) and, on non-Step-3 D's, the New-E entry (e_net), BOTH exited with the V3 ZZTRAIL (structural stop +
0.05% trail + arm 0.4% -> lock 0.1%, fee 0.10) — the exit as applied on the terminal. We only repoint its data
loaders at the reconstruction (light 1m, no levels needed; 4h keeps levels for the zone).

FADE = what the strategy SKIPS (dimmed grey on the chart); NON-FADE = what it TAKES (bright):
  D non-faded = Step-3 direct-D (cyan/orange tier + directional 4H zone)      | D faded = every other D
  E non-faded = New-E in the frozen TAKE combo set (forward-audit E_OK/E_CYAN) | E faded = every other New-E
Account: $200k, 10% margin x10 lev = 100% of balance notional/trade, compounded. win = net>0 (three-outcome W/BE/L too).

Run: python study/pivot_de_fade_1m.py
"""
from __future__ import annotations
import os, sys, glob, gzip, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.pivot_v3_de_zone_pdf as V3
from app import bar_quantiles
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
B0 = 200_000.0; MARGIN_FRAC = 0.10; LEVERAGE = 10.0; BE = V3.BE
_KEEP = ("open_price", "close_price", "high", "low", "poc_price", "end_time", "start_time",
         "buy_vol", "sell_vol", "curr_vol", "buyer_er", "seller_er")
_MAX = int(os.environ.get("PIVOT_MAX", "0"))     # smoke-test cap on 1m buckets (0 = all)

# Frozen Step-4 E TAKE set (side . D-zone -> E-zone), verbatim from pivot_v3_forward_audit.
E_OK = {("Buy D", "buy area", "body"), ("Sell D", "sell area", "body"),
        ("Buy D", "below buy area", "buy area"), ("Sell D", "above sell area", "sell area")}
E_CYAN = {("Buy D", "body", "sell area"), ("Sell D", "body", "buy area")}


def ematch(r):
    k = (r["side"], r["d_zone"], r["e_zone"])
    return (k in E_OK) or (r["tier"] == "cyan/orange" and k in E_CYAN)


def load_1m_recon():
    """Stream the recon 1m chunks -> light DB-form dicts (no levels; the pivot's series don't need them)."""
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
                out.append({k: d.get(k) for k in _KEEP if k in d})
                if _MAX and len(out) >= _MAX:
                    break
        if _MAX and len(out) >= _MAX:
            break
    out.sort(key=lambda d: float(d["start_time"]))
    return out


def load_4h_recon():
    _, rows, _ = load_archive("4h", root=RECON)
    by = {}
    for b in rows:
        if b.get("levels"):
            by[float(b["end_time"])] = b
    b4 = [by[k] for k in sorted(by)]
    et = [float(b["end_time"]) for b in b4]; vlo = []; vhi = []; lw = []; hg = []
    for b in b4:
        q = bar_quantiles.vq(b["levels"]); vlo.append(float(q[0])); vhi.append(float(q[2]))
        lw.append(float(b["low"])); hg.append(float(b["high"]))
    return et, vlo, vhi, lw, hg


def acct(nets):
    bal = B0
    for x in nets:
        bal *= (1.0 + x / 100.0)                  # net is already %; 100% notional -> account moves by net
        if bal <= 0:
            return 0.0
    return bal


def report(label, recs, netkey, timekey):
    recs = sorted((r for r in recs if r.get(netkey) is not None), key=lambda r: r[timekey])
    nets = [r[netkey] for r in recs]
    n = len(nets)
    if n == 0:
        print("  %-16s n=0" % label); return
    a = np.asarray(nets)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    winpos = 100.0 * (a > 0).sum() / n
    bal = acct(nets); pnl = bal - B0
    print("  %-16s n=%4d  win %5.1f%% (W %d/BE %d/L %d)  mean %+.4f%%   END $%9.0f   P&L $%+9.0f (%+.1f%%)"
          % (label, n, winpos, w, b, l, a.mean(), bal, pnl, pnl / B0 * 100))


def main():
    V3.load_1m = load_1m_recon
    V3.load_4h = load_4h_recon
    print("building V3 records on the recon (light 1m + recon 4h) ...", flush=True)
    rows = V3.build_records()
    E = [r for r in rows if r["e_net"] is not None]
    print("=" * 104)
    print("PIVOT V3 D & E entries: FADED vs NON-FADED  |  recon 1m %s  |  %d fires -> D=%d, New-E=%d"
          % (("(capped %d)" % _MAX) if _MAX else "(18 months)", len(rows), len(rows), len(E)))
    print("Exit = terminal V3 ZZTRAIL (struct stop / 0.05%% trail / arm 0.4%%->lock 0.1%%, fee 0.10). Account $%.0f @ 10%%x10." % B0)
    print("=" * 104)
    print("D entries (market at detection):")
    report("D  NON-FADED", [r for r in rows if r["step3"]], "d_net", "d_time")
    report("D  FADED", [r for r in rows if not r["step3"]], "d_net", "d_time")
    print("E entries (New-E, WAIT-baseline):")
    report("E  NON-FADED", [r for r in E if ematch(r)], "e_net", "e_time")
    report("E  FADED", [r for r in E if not ematch(r)], "e_net", "e_time")
    print("-" * 104)
    print("FADED = setups the strategy SKIPS (dimmed on the chart); NON-FADED = TAKEN. Three-outcome W>+.05/BE/L<-.05, fee 0.10.")
    print("CAVEAT: reconstructed 1m footprint (independent bucketing, OI approximate). The pivot's 5 legs are ALL microstructure-derived.")


if __name__ == "__main__":
    main()
