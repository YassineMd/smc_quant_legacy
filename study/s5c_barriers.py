"""S5c-BARRIERS — barrier simulation on the EXACT S5c fire sets (pre-registered outcome re-measure;
multiplicity +4 cells -> program counter 462).

INPUT: s5c_episodes_locked.csv + s5c_episodes_unlocked.csv exactly as committed — no re-detection.
Entry = fire-bar close (the CSV baseline, asserted equal to the bar's close on the merged tape).

SIMULATION (independent per fire — trades MAY OVERLAP in time, unlike the episode windows):
LONG TP = entry x 1.005, SL = entry x 0.997; SHORT mirrored (TP -0.5%, SL +0.3%). Walk subsequent
1m bars with the S1 path-walker conventions VERBATIM (study/scalp_geometry.py):
 * walk starts at the bar AFTER the fire bar; a bar is inside the horizon if its START time is
   within entry_ts + 6h (labeler convention);
 * touch = bar high/low REACHES the level (inclusive >=/<=);
 * one bar touching BOTH barriers -> SL (frozen ambiguity rule), flagged and counted;
 * resolution time = touch-bar START minus entry ts (the S1 up_t/dn_t convention);
 * unresolved at the 6h cap -> UNRESOLVED, counted, excluded from win rate;
 * horizon extending past the data end while unresolved -> END-OF-DATA, excluded, counted.

REFERENCES printed with every table: geometric null 37.5% (= 0.3/0.8) and the fee-adjusted
breakeven 50.0% at taker 0.10% RT (net win +0.40% vs net loss -0.40%). Expectancy per trade:
gross = p x 0.5 - (1-p) x 0.3; net = gross - 0.10. Underpowered rule: n < 20 -> counts only,
no verdict language. HARD STOP — no threshold variants, no alternative barriers.
"""
import os, sys, csv, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict          # noqa: E402
from m10_sweep_s5b import load_merged                  # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
TP, SL = 0.005, 0.003
H_S = 6 * 3600.0
FEE = 0.10                                             # taker round-trip, % per trade
NULL = 100.0 * SL / (TP + SL)                          # 37.5
BREAKEVEN = 100.0 * (SL + FEE / 100.0) / (TP + SL)     # 50.0 at 0.10% RT
UNDERPOWERED_N = 20
CELLS = (("locked", "long"), ("locked", "short"), ("unlocked", "long"), ("unlocked", "short"))


def main():
    t0 = time.time()
    bids, raws, _pd, _g, _d = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    st = np.array([float(d["start_time"]) for d in raws])
    b0 = bids[0]

    fires = []
    for v in ("locked", "unlocked"):
        with open(os.path.join(OUT, "s5c_episodes_%s.csv" % v), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                fires.append(dict(variant=v, side=r["side"], ts=float(r["ts"]),
                                  bid=int(r["bucket_id"]), entry=float(r["baseline"])))
    print("[%2.0fs] %d fires loaded (no re-detection)" % (time.time() - t0, len(fires)), flush=True)

    trades = []
    for tr in fires:
        i = tr["bid"] - b0
        assert bids[i] == tr["bid"] and abs(cl[i] - tr["entry"]) < 1e-9 and abs(et[i] - tr["ts"]) < 1e-6
        entry = tr["entry"]; t_ent = et[i]
        if tr["side"] == "long":
            up_lvl, dn_lvl = entry * (1.0 + TP), entry * (1.0 - SL)
            tp_hit = lambda j: hi[j] >= up_lvl; sl_hit = lambda j: lo_[j] <= dn_lvl
        else:
            dn_lvl, up_lvl = entry * (1.0 - TP), entry * (1.0 + SL)
            tp_hit = lambda j: lo_[j] <= dn_lvl; sl_hit = lambda j: hi[j] >= up_lvl
        outcome, minutes, ambig = None, None, False
        j = i + 1
        while j < n and st[j] <= t_ent + H_S:
            a, b = tp_hit(j), sl_hit(j)
            if a or b:
                ambig = a and b
                outcome = "SL" if b else "TP"          # both in one bar -> SL (frozen S1 rule)
                minutes = (st[j] - t_ent) / 60.0
                break
            j += 1
        if outcome is None:
            outcome = "EOD" if t_ent + H_S > et[-1] else "UNRESOLVED"
        trades.append(dict(tr, outcome=outcome, minutes=minutes, ambig=ambig))

    with open(os.path.join(OUT, "s5c_barrier_trades.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "bucket_id", "variant", "side", "entry", "outcome",
                    "minutes_to_resolution", "ambiguous_flag"])
        for t in trades:
            w.writerow([round(t["ts"], 3), t["bid"], t["variant"], t["side"], t["entry"],
                        t["outcome"], round(t["minutes"], 2) if t["minutes"] is not None else "",
                        int(t["ambig"])])

    def cell_stats(v, s):
        cs = [t for t in trades if t["variant"] == v and t["side"] == s]
        ntp = sum(1 for t in cs if t["outcome"] == "TP")
        nsl = sum(1 for t in cs if t["outcome"] == "SL")
        nun = sum(1 for t in cs if t["outcome"] == "UNRESOLVED")
        neod = sum(1 for t in cs if t["outcome"] == "EOD")
        namb = sum(1 for t in cs if t["ambig"])
        res = ntp + nsl
        p = 100.0 * ntp / res if res else float("nan")
        mins = [t["minutes"] for t in cs if t["minutes"] is not None]
        in30 = sum(1 for m in mins if m <= 30.0)
        gsum = 0.5 * ntp - 0.3 * nsl
        nsum = gsum - 0.10 * res
        return dict(cs=cs, n=len(cs), ntp=ntp, nsl=nsl, nun=nun, neod=neod, namb=namb, res=res,
                    p=p, gsum=gsum, nsum=nsum,
                    g_exp=(p / 100.0 * 0.5 - (1 - p / 100.0) * 0.3) if res else float("nan"),
                    med=float(np.median(mins)) if mins else float("nan"),
                    p90=float(np.percentile(mins, 90)) if mins else float("nan"),
                    in30=in30, beyond=len(mins) - in30)

    stats = {c: cell_stats(*c) for c in CELLS}
    for c in CELLS:
        x = stats[c]
        print("  %s-%s: n=%d TP/SL/UN/EOD %d/%d/%d/%d ambig %d | TP%%(res) %s | gross sum %+.2f%% net %+.2f%%"
              % (c[0], c[1], x["n"], x["ntp"], x["nsl"], x["nun"], x["neod"], x["namb"],
                 "%.1f" % x["p"] if x["res"] else "-", x["gsum"], x["nsum"]), flush=True)

    md = ["# S5c-BARRIERS — 0.5/0.3/6h barrier re-measure of the S5c fires", "",
          "_**Pre-registered outcome re-measure on the EXACT S5c episode sets (no re-detection);"
          " multiplicity +4 cells -> program counter 462.** Entry = fire-bar close; LONG TP +0.5%% /"
          " SL -0.3%%, SHORT mirrored; S1 path-walker conventions verbatim (inclusive touch, bar-start"
          " horizon test, one bar spanning BOTH barriers -> SL, flagged). 6h cap; UNRESOLVED excluded"
          " from win rate; end-of-data excluded, counted. **Trades are simulated independently and"
          " MAY OVERLAP in time** — unlike the S5c episode windows, this is a per-fire outcome"
          " measure, not a sequential book. References for every table: geometric null **%.1f%%**"
          " (0.3/0.8); fee-adjusted breakeven **%.1f%%** at taker %.2f%% RT (net win +0.40%% vs net"
          " loss -0.40%%). Expectancy: gross = p x 0.5 - (1-p) x 0.3; net = gross - 0.10._"
          % (NULL, BREAKEVEN, FEE), "",
          "## LOCKED vs UNLOCKED — summary", "",
          "| cell | n | TP | SL | unres | eod | ambig | TP%% (res) | vs null %.1f | vs BE %.1f | "
          "gross E/trade | net E/trade | gross sum | net sum | status |" % (NULL, BREAKEVEN),
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in CELLS:
        x = stats[c]
        under = x["n"] < UNDERPOWERED_N
        md.append("| %s-%s | %d | %d | %d | %d | %d | %d | %s | %s | %s | %s | %s | %+.2f%% | %+.2f%% | %s |" % (
            c[0].upper(), c[1], x["n"], x["ntp"], x["nsl"], x["nun"], x["neod"], x["namb"],
            "%.1f%%" % x["p"] if x["res"] else "-",
            ("%+.1f" % (x["p"] - NULL)) if x["res"] else "-",
            ("%+.1f" % (x["p"] - BREAKEVEN)) if x["res"] else "-",
            ("%+.3f%%" % x["g_exp"]) if x["res"] else "-",
            ("%+.3f%%" % (x["g_exp"] - 0.10)) if x["res"] else "-",
            x["gsum"], x["nsum"], "UNDERPOWERED" if under else "powered"))
    md.append("")
    for c in CELLS:
        v, s = c; x = stats[c]
        under = x["n"] < UNDERPOWERED_N
        md += ["## %s-%s — %d trades%s" % (v.upper(), s, x["n"],
                                           " (UNDERPOWERED: counts only, no verdict language)" if under else ""), "",
               "TP %d / SL %d / unresolved %d / end-of-data %d; ambiguous-bar SLs: %d. TP rate of "
               "resolved: %s (references: null %.1f%%, fee breakeven %.1f%%). Resolution time med "
               "%s / p90 %s min; resolved inside the original 30-min window: %d, beyond: %d."
               % (x["ntp"], x["nsl"], x["nun"], x["neod"], x["namb"],
                  "%.1f%%" % x["p"] if x["res"] else "-", NULL, BREAKEVEN,
                  "%.1f" % x["med"] if x["med"] == x["med"] else "-",
                  "%.1f" % x["p90"] if x["p90"] == x["p90"] else "-", x["in30"], x["beyond"]), ""]
        if x["res"]:
            md += ["Expectancy/trade: gross %+.3f%%, net %+.3f%%. Sums over the %d resolved trades: "
                   "gross %+.2f%%, net %+.2f%%." % (x["g_exp"], x["g_exp"] - 0.10, x["res"],
                                                    x["gsum"], x["nsum"]), ""]
        md += ["| ts (UTC) | bucket | entry | outcome | min to res | ambig |", "|---|---|---|---|---|---|"]
        for t in x["cs"]:
            md.append("| %s | %d | %.2f | %s | %s | %s |" % (
                time.strftime("%m-%d %H:%M", time.gmtime(t["ts"])), t["bid"], t["entry"], t["outcome"],
                "%.1f" % t["minutes"] if t["minutes"] is not None else "-", "Y" if t["ambig"] else ""))
        md.append("")
    md += ["## Honest flags",
           "- Same 5.26-day tape as S5c; the barrier geometry (0.5/0.3/6h) is the program's frozen"
           " original — nothing tuned, no alternative barriers run.",
           "- Overlapping trades share tape segments; counts are per-fire, not portfolio-independent.",
           "- Three of four cells are under the n=20 bar: counts only, judged on forward snapshots.",
           "", "## HARD STOP", "Judged once. No threshold variants, no alternative barriers."]
    with open(os.path.join(OUT, "analysis_report_S5c_barriers.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%2.0fs] report + trades CSV written" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
