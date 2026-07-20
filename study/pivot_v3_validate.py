"""PIVOT V3 — SPLIT-HALF + MONTE CARLO validation of the FROZEN rule (never re-tuned).

Reconstructs the frozen TAKEN trades EXACTLY as study/pivot_v3_forward_audit.py does (Path A direct-D +
Path B New-E combo set), splits at freeze_ts, and reports:
  (1) IN-SAMPLE summary (must reproduce the n=56 baseline) + a TIME split-half (H1 vs H2) consistency check
      — is the in-sample edge carried by both halves or one lucky sub-period?
  (2) MONTE CARLO on the in-sample per-trade nets (additive fixed-notional accounting: $ = net% x 10, matching
      the audit): (A) BOOTSTRAP (resample n trades WITH replacement, 20k) -> P(profit), CI on mean/trade, total$
      percentiles; (B) PERMUTATION (shuffle trade ORDER, 20k) -> max-drawdown distribution (sequence risk).
  (3) FORWARD (post-freeze) block for context = the ONLY true out-of-sample so far.

CAVEAT: like all MC, this treats the observed in-sample trades as representative — it quantifies SAMPLING +
SEQUENCE luck ONLY, NOT regime/overfitting risk. The split-half is a WITHIN-sample consistency check (the rule
was frozen after seeing all of it), NOT true OOS; the forward log is the real OOS. NEVER re-tune from this.

Run: python study/pivot_v3_validate.py
"""
import os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import pivot_v3_de_zone_pdf as V3

FREEZE = os.path.join(HERE, "out", "pivot_v3_freeze.json")
# Frozen Step-4 E TAKE set (side . D-zone -> E-zone), verbatim from the forward audit. Never re-tuned.
E_OK = {("Buy D", "buy area", "body"), ("Sell D", "sell area", "body"),
        ("Buy D", "below buy area", "buy area"), ("Sell D", "above sell area", "sell area")}
E_CYAN = {("Buy D", "body", "sell area"), ("Sell D", "body", "buy area")}
BE = 0.05        # three-outcome NET band: winner > +0.05 / breakeven |.|<=0.05 / loser < -0.05
SIMS = 20000
np.random.seed(42)


def ematch(r):
    k = (r["side"], r["d_zone"], r["e_zone"])
    return (k in E_OK) or (r["tier"] == "cyan/orange" and k in E_CYAN)


def taken_trades():
    """[(entry_time, net, path)] — the exact frozen TAKE set (Path A direct-D + Path B New-E combo)."""
    out = []
    for r in V3.build_records():
        if r["step3"]:
            out.append((r["d_time"], r["d_net"], "A"))
        elif r["e_net"] is not None and ematch(r):
            out.append((r["e_time"], r["e_net"], "B"))
    return out


def stats(nets):
    a = np.asarray(nets, float); n = len(a)
    if n == 0:
        return dict(n=0, w=0, b=0, l=0, mean=0.0, totpct=0.0, tot=0.0, t=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    t = a.mean() / (a.std(ddof=1) / np.sqrt(n)) if (n > 1 and a.std(ddof=1) > 0) else 0.0
    return dict(n=n, w=w, b=b, l=l, mean=float(a.mean()), totpct=float(a.sum()),
                tot=float(a.sum()) * 10.0, t=float(t))


def line(tag, s):
    return ("  %-18s n=%-3d | W %-2d BE %-2d L %-2d | net %+.3f%%/tr | TOT %+.2f%% ($%+.0f) | t=%+.2f"
            % (tag, s["n"], s["w"], s["b"], s["l"], s["mean"], s["totpct"], s["tot"], s["t"]))


def main():
    fz = json.load(open(FREEZE)); ft = fz["freeze_ts"]
    tk = sorted(taken_trades(), key=lambda x: x[0])
    ins = np.array([g for (t, g, p) in tk if t <= ft])
    fwd = np.array([g for (t, g, p) in tk if t > ft])

    print("PIVOT V3 validation  |  freeze_ts %d (%s)" % (ft, fz["freeze_utc"]))
    print("  baseline (frozen): n=56 | 29W/10BE/17L | +0.135%/tr | $+75 | t+2.06\n")
    print("=" * 96); print("(1) IN-SAMPLE + split-half (time)"); print("=" * 96)
    print(line("in-sample (all)", stats(ins)))
    mid = len(ins) // 2
    print(line("  split H1 (early)", stats(ins[:mid])))
    print(line("  split H2 (late)", stats(ins[mid:])))
    print(line("FORWARD (true OOS)", stats(fwd)))

    r = ins; n = len(r)
    print("\n" + "=" * 96); print("(2) MONTE CARLO on the %d in-sample trades  (additive: $ = net%% x 10)" % n)
    print("=" * 96)
    # (A) bootstrap with replacement
    idx = np.random.randint(0, n, size=(SIMS, n)); samp = r[idx]
    totals = samp.sum(axis=1); dollars = totals * 10.0; means = samp.mean(axis=1)
    pct = np.percentile(dollars, [5, 25, 50, 75, 95])
    print("  (A) BOOTSTRAP  total-$ distribution over %d trades:" % n)
    print("      p5 $%+.0f | p25 $%+.0f | median $%+.0f | p75 $%+.0f | p95 $%+.0f"
          % (pct[0], pct[1], pct[2], pct[3], pct[4]))
    print("      P(profit) = %.1f%%   P(>=baseline $75) = %.1f%%   P(loss < -$25) = %.1f%%"
          % (100 * np.mean(totals > 0), 100 * np.mean(dollars >= 75), 100 * np.mean(dollars < -25)))
    print("      bootstrap P(mean/trade > 0) = %.1f%%   95%% CI mean/trade [%+.3f, %+.3f]%%"
          % (100 * np.mean(means > 0), np.percentile(means, 2.5), np.percentile(means, 97.5)))
    # (B) permutation of trade order -> additive-equity drawdown risk
    perm = np.array([np.random.permutation(r) for _ in range(SIMS)])
    eq = np.cumsum(perm, axis=1); peak = np.maximum.accumulate(eq, axis=1)
    dd = np.max(peak - eq, axis=1) * 10.0
    aeq = np.cumsum(r); add_ = np.max(np.maximum.accumulate(aeq) - aeq) * 10.0
    ddp = np.percentile(dd, [50, 75, 95, 99])
    print("  (B) PERMUTATION  max-drawdown distribution ($):")
    print("      median $%.0f | p75 $%.0f | p95 $%.0f | p99 $%.0f   (actual-order DD $%.0f)"
          % (ddp[0], ddp[1], ddp[2], ddp[3], add_))

    print("\n" + "=" * 96); print("(3) READ")
    print("  Split-half PASS if BOTH halves net>0 with similar W/BE/L. MC PASS if P(profit)>~90%% and the 95%%")
    print("  CI on mean/trade stays > 0. Forward block is the real test and is still accruing (n<40).")


if __name__ == "__main__":
    main()
