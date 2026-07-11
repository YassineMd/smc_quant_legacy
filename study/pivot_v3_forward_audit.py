"""PIVOT V3 — FORWARD AUDIT (the canonical periodic check for the frozen Pivot V3 strategy).

Reads study/out/pivot_v3_freeze.json (freeze_ts + rules + in-sample baseline + pre-declared PASS/FAIL). Runs the
FROZEN V3 rule on the merged snapshot tape, splits each TAKEN trade at freeze_ts (entry end_time > freeze = FORWARD),
prints in-sample vs forward W/BE/L + net + total$ + t, a PASS/FAIL/CONTINUE verdict + degradation flag, and APPENDS
one dated row to study/out/pivot_v3_forward_log.md (append-only, never edited).

The frozen rule = pivot_v3_de_zone_pdf.build_records (Step 1-2 detect+tier, Step 3 direct-D, Step 4 New-E) plus the
E-combo TAKE set below. Pull a fresh snapshot first (study/pull_snapshot.ps1) so forward bars exist. NEVER re-tune —
any param change means a NEW freeze (bump pivot_v3_freeze.json's freeze_ts, discard the prior forward log).

Run: python study/pivot_v3_forward_audit.py
"""
import os, sys, json, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import pivot_v3_de_zone_pdf as V3   # the frozen V3 rule (detect + first-print tier + walk + New-E)

FREEZE = os.path.join(HERE, "out", "pivot_v3_freeze.json")
LOG = os.path.join(HERE, "out", "pivot_v3_forward_log.md")

# Frozen Step-4 E TAKE set (side . D-zone -> E-zone). 4 any-tier + 2 cyan/orange-only. Never re-tuned.
E_OK = {("Buy D", "buy area", "body"), ("Sell D", "sell area", "body"),
        ("Buy D", "below buy area", "buy area"), ("Sell D", "above sell area", "sell area")}
E_CYAN = {("Buy D", "body", "sell area"), ("Sell D", "body", "buy area")}
BE = 0.05   # three-outcome NET band: winner > +0.05 / breakeven |.|<=0.05 / loser < -0.05


def ematch(r):
    """Does this New-E row fall in the frozen TAKE set?"""
    k = (r["side"], r["d_zone"], r["e_zone"])
    return (k in E_OK) or (r["tier"] == "cyan/orange" and k in E_CYAN)


def stats(nets):
    a = np.asarray(nets, float); n = len(a)
    if n == 0:
        return dict(n=0, w=0, b=0, l=0, mean=0.0, tot=0.0, t=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    t = a.mean() / (a.std(ddof=1) / np.sqrt(n)) if (n > 1 and a.std(ddof=1) > 0) else 0.0
    return dict(n=n, w=w, b=b, l=l, mean=float(a.mean()), tot=float(a.sum()) * 10.0, t=float(t))


def main():
    fz = json.load(open(FREEZE)); ft = fz["freeze_ts"]
    rows = V3.build_records()

    taken = []   # (entry_time, net, path)
    for r in rows:
        if r["step3"]:                                            # Path A: direct-D (cyan/orange + directional zone)
            taken.append((r["d_time"], r["d_net"], "A"))
        elif r["e_net"] is not None and ematch(r):                # Path B: New-E in the frozen combo set
            taken.append((r["e_time"], r["e_net"], "B"))

    ins = [g for (t, g, _) in taken if t <= ft]
    fwd = [g for (t, g, _) in taken if t > ft]
    fa = [g for (t, g, p) in taken if p == "A" and t > ft]
    fb = [g for (t, g, p) in taken if p == "B" and t > ft]
    si, sf, sa, sb = stats(ins), stats(fwd), stats(fa), stats(fb)

    def line(tag, s):
        return "  %-16s n=%-3d | W %-2d BE %-2d L %-2d | net %+.3f%% | TOT $%+.0f | t=%+.2f" % (
            tag, s["n"], s["w"], s["b"], s["l"], s["mean"], s["tot"], s["t"])

    print("PIVOT V3 forward audit  |  freeze_ts %d (%s)" % (ft, fz["freeze_utc"]))
    print("  baseline (frozen): n=56 | 29W/10BE/17L | +0.135%% | $+75 | t+2.06")
    print(line("in-sample", si))
    print(line("FORWARD (all)", sf))
    print(line("  fwd Path A", sa))
    print(line("  fwd Path B", sb))

    verdict = "CONTINUE (forward n<40)"
    if sf["n"] >= 40:
        if sf["mean"] > 0 and sf["t"] >= 1.5:
            verdict = "PASS"
        elif sf["mean"] <= 0:
            verdict = "FAIL"
        else:
            verdict = "CONTINUE (n>=40 but t<1.5)"
    warn = " | DEGRADE-WARN" if (sf["n"] >= 30 and sf["mean"] < 0.068) else ""
    print("  VERDICT: %s%s" % (verdict, warn))

    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    row = "| %s | %d | %+.3f%% | %+.0f | %+.2f | %d/%d/%d | %s%s |\n" % (
        stamp, sf["n"], sf["mean"], sf["tot"], sf["t"], sf["w"], sf["b"], sf["l"], verdict, warn)
    fresh = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8") as f:
        if fresh:
            f.write("# PIVOT V3 - forward log (append-only)\n\n")
            f.write("Frozen 2026-07-10; freeze_ts %d = %s. Baseline n=56, 29W/10BE/17L, +0.135%%/tr, $+75, t+2.06.\n" % (ft, fz["freeze_utc"]))
            f.write("PASS: fwd n>=40 & net>0 & t>=1.5 | FAIL: n>=40 & net<=0 | degrade-warn: n>=30 & net<+0.068.\n\n")
            f.write("| run (UTC) | fwd n | net/tr | tot$ | t | W/BE/L | verdict |\n|---|---|---|---|---|---|---|\n")
        f.write(row)
    print("  appended -> %s" % os.path.relpath(LOG, REPO))


if __name__ == "__main__":
    main()
