"""STACK losers list (user 2026-09-04): every STACK trade (opposing whale run over x 1m-confirm in the
cheap/expensive third, 30m bucket, weekday NY, parent bracket) across FULL recon 2025 / 2026H1 and the
daemon OOS, resolved at RR1:1.5 with the SAME non-overlap taken() accounting as the report tables;
LOSERS printed with everything needed to eyeball them in replay + CSV to study/out/rr_stack_losers.csv.
python study/radarrun_stack_losers.py"""
import os, sys, json, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np

from study.radarrun_pullback_1m import _f, resolve, CACHE_30, OUT
from study.radarrun_pullback_1mbkt_ema import CLOCK_NPZ
from study.radarrun_confirm_daemon_oos import OOS_ROOT, JUL1, SEP1
from study.radarrun_stack_whale_confirm import whale_flags, CELLS, BP_USD, CACHE_D30, SPLIT
from app import bigprint_store

bigprint_store.CACHE_FLOOR_USD = BP_USD
ZONE = {0: "beyond-dn", 1: "cheap", 2: "equilib", 3: "expensive", 4: "beyond-up", 5: "inverted", -1: "n/a"}
LOCAL = timedelta(hours=1)                         # user's clock (UTC+1)
H24 = bool(os.environ.get("RR_24H"))               # RR_24H=1 -> the 24h/7d run (study/radarrun_stack_full24h.py)
F_RECON = "rr_confirm_full24h_trades.json" if H24 else "rr_confirm_full_trades.json"
F_OOS = "rr_confirm_daemon_oos24h_trades.json" if H24 else "rr_confirm_daemon_oos_trades.json"
F_CSV = "rr_stack_losers_24h.csv" if H24 else "rr_stack_losers.csv"


def fmt(t, local=False):
    d = datetime.fromtimestamp(t, tz=timezone.utc) + (LOCAL if local else timedelta())
    return d.strftime("%Y-%m-%d %H:%M")


def ny_weekday(et):
    d = datetime.fromtimestamp(et, tz=timezone.utc)
    return d.weekday() < 5 and 13 * 3600 <= (et % 86400) < 21 * 3600


def main():
    from study.archive_loader import load_archive
    stack = CELLS[4][1]
    rows = []
    # ---- recon
    tr = json.load(open(os.path.join(OUT, F_RECON)))
    f30 = json.load(open(CACHE_30))
    A30 = sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    span = {round(et, 2): (_f(A30[b].get("start_time")), et) for (b, et, s, e, sl) in f30}
    del A30
    whale_flags(tr, span)
    z = np.load(CLOCK_NPZ)
    arr_r = (z["t"], z["h"], z["l"], z["c"])
    # ---- daemon OOS
    trd = json.load(open(os.path.join(OUT, F_OOS)))
    fd = json.load(open(CACHE_D30))
    A30d = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    A30d = [b for b in A30d if _f(b.get("start_time")) < SEP1]
    span_d = {round(et, 2): (_f(A30d[b].get("start_time")), et) for (b, et, s, e, sl) in fd}
    del A30d
    whale_flags(trd, span_d)
    A1 = sorted(load_archive("1m", root=OOS_ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    A1 = [b for b in A1 if JUL1 <= _f(b.get("start_time")) < SEP1]
    arr_d = (np.array([_f(b.get("start_time")) for b in A1]), np.array([_f(b.get("high")) for b in A1]),
             np.array([_f(b.get("low")) for b in A1]), np.array([_f(b.get("close", b.get("close_price"))) for b in A1]))
    del A1

    for period, pool, arrs, spans in (("2025", [x for x in tr if x["t"] < SPLIT], arr_r, span),
                                      ("2026H1", [x for x in tr if x["t"] >= SPLIT], arr_r, span),
                                      ("OOS", trd, arr_d, span_d)):
        T1S, H1, L1, C1 = arrs
        busy = -1.0
        for x in sorted((y for y in pool if stack(y)), key=lambda y: y["t"]):
            if x["t"] < busy:
                continue                                       # non-overlap taken(): same as the tables
            risk = abs(x["e"] - x["sl"]) / x["e"]
            if risk <= 0:
                continue
            net, tx = resolve(x["s"], x["e"], x["sl"], x["t"], "rr", 1.5, T1S, H1, L1, C1)
            busy = tx
            st, et = spans[round(x["t"], 2)]
            prints = bigprint_store.load_prints(st, et, BP_USD)
            opp_side = 0 if x["s"] > 0 else 1
            opp = sorted((p for p in prints if p[3] == opp_side), key=lambda p: -p[2])
            tp = x["e"] * (1 + x["s"] * 1.5 * risk)
            outcome = "SL" if net < -risk * 0.9 else ("TP" if net > 0 else "EOD/other")
            net02, _ = resolve(x["s"], x["e"], x["sl"], x["t"], "fix", 0.0024, T1S, H1, L1, C1)   # same trade, 0.2% exit (info)
            rows.append(dict(period=period, badge_utc=fmt(x["t"]), badge_local=fmt(x["t"], True),
                             bucket_start_utc=fmt(st), ny_weekday=int(ny_weekday(x["t"])),
                             side="LONG" if x["s"] > 0 else "SHORT",
                             entry=round(x["e"], 2), sl=round(x["sl"], 2), tp15=round(tp, 2),
                             risk_pct=round(risk * 100, 3), net_pct=round(net * 100, 3), outcome=outcome,
                             net02_pct=round(net02 * 100, 3),
                             exit_utc=fmt(tx), confirm_zone=ZONE.get(int(x.get("cz", -1)), "n/a"),
                             whales=len(opp),
                             top_whale="$%.0fK @ %.2f %s" % (opp[0][2] / 1e3, opp[0][1], fmt(opp[0][0])[11:]) if opp else ""))
    path = os.path.join(OUT, F_CSV)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    n = len(rows); nl = sum(1 for r in rows if r["net_pct"] < -0.02); nw = sum(1 for r in rows if r["net_pct"] > 0.02)
    print("STACK @ RR1:1.5 (%s) — taken %d | winners %d | losers %d | avg %+.3f%%  (all rows -> %s)\n"
          % ("24h/7d" if H24 else "weekday NY", n, nw, nl, sum(r["net_pct"] for r in rows) / max(1, n), path))
    print("%-7s %-16s %-16s %-3s %-5s %8s %8s %8s %7s %7s %-10s %s" % (
        "period", "badge UTC", "badge local+1", "NY", "side", "entry", "SL", "TP1.5", "net%", "net0.2%", "zone", "top opposing whale"))
    for r in rows:
        if r["net_pct"] < -0.02:
            print("%-7s %-16s %-16s %-3s %-5s %8.2f %8.2f %8.2f %+7.3f %+7.3f %-10s %s" % (
                r["period"], r["badge_utc"], r["badge_local"], "NY" if r["ny_weekday"] else "-", r["side"],
                r["entry"], r["sl"], r["tp15"], r["net_pct"], r["net02_pct"], r["confirm_zone"], r["top_whale"]))


if __name__ == "__main__":
    main()
