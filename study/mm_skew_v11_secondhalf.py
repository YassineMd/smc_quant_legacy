"""MMXSKEW v1.1 (POST delta-freeze 2026-07-24) + a SECOND-HALF AGREEMENT filter.

Hypothesis (user, 2026-07-24): the bucket must be FINISHING in the trade's direction — in the second half of the
bucket **split at the 50%-VOLUME mark**, BOTH the net delta and the price change must agree with the side:
    LONG  : d2 > 0  AND  dP_h2 > 0
    SHORT : d2 < 0  AND  dP_h2 < 0
where (app/absorption._halves, the exact terminal readouts):
    d2    = (buy_vol - sell_vol) - delta_h1      -> the "Δ-accel ( d1 / d2 )" second leg   [% of curr_vol; sign-identical]
    dP_h2 = (close - price_h1)/price_h1 * 100    -> the "ΔP ( p1 / p2 )" second leg

SECONDARY CELL (the attached screenshot showed the OTHER half-split row): Δ↑/Δ↓ = net delta of the upper/lower
half of the PRICE range (vertical split at (high+low)/2), as % of curr_vol. Tested separately, clearly labelled.

BASE = the FROZEN v1.1 gate incl. the 2026-07-24 delta freeze:
    LONG  close>open & skew>0 & spread>=+35 & 0 < delta <= 15
    SHORT close<open & skew<0 & spread<=-35 & delta < 0

METHOD (per the standing rules):
  * stats ONLY from taken() non-overlap [canonical-taken-basis]; the filter is applied BEFORE a signal claims the
    slot, so the chain re-links exactly as it would live.
  * LIKE-FOR-LIKE: the halves fields are missing on some buckets, so the baseline is ALSO restricted to the
    EVALUABLE subset. Comparing an evaluable-only filter against an all-signals baseline would confound
    "filter works" with "the data-available subset differs".
  * DISJOINT bands + an OPPOSITE-filter control [disjoint-bands-not-cumulative-ladders], exact binomial vs
    break-even and Fisher vs the complement [split-half-gate-is-near-vacuous].

Run: python study/mm_skew_v11_secondhalf.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import archive
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR

RRS = (1.0, 1.5)
FEE = 0.0008


# ---------------------------------------------------------------- data
def build():
    """FM's frozen matrix + delta_h1/price_h1 merged from the 1m-reconstruction overlay, then the derived
    second-half legs (d2 as % of curr_vol, dP_h2) and the vertical Δ↑/Δ↓ split."""
    A, first, _, _ = FM.build()
    ov = archive._load_overlay()
    for b in A:
        pair = ov.get("1h|%.3f" % float(b.get("start_time", 0.0) or 0.0))
        if pair:
            if b.get("delta_h1") is None and pair[0] is not None:
                b["delta_h1"] = pair[0]
            if b.get("price_h1") is None and pair[1] is not None:
                b["price_h1"] = pair[1]
        cv = float(b.get("curr_vol", 0.0)) or 1.0
        tot = float(b.get("buy_vol", 0.0)) - float(b.get("sell_vol", 0.0))
        dh1 = b.get("delta_h1"); ph1 = b.get("price_h1")
        # --- PRIMARY: split at the 50%-VOLUME mark (mirrors app/absorption._halves exactly) ---
        b["d2pct"] = ((tot - float(dh1)) / cv * 100.0) if dh1 is not None else None
        b["p1"] = (((float(ph1) - b["o"]) / b["o"] * 100.0)
                   if (ph1 is not None and float(ph1) > 0 and b["o"] > 0) else None)
        b["p2"] = (((b["c"] - float(ph1)) / float(ph1) * 100.0)
                   if (ph1 is not None and float(ph1) > 0 and b["c"] > 0) else None)
        # --- SECONDARY: vertical split at the price midpoint (the Δ↑/Δ↓ screenshot row) ---
        lv = b.get("levels") or {}
        h, l = b["h"], b["l"]
        if lv and h > 0 and l > 0 and h >= l:
            mid = (h + l) / 2.0; ub = us = lb = ls = 0.0
            for ps, lvv in lv.items():
                try:
                    p = float(ps)
                except (TypeError, ValueError):
                    continue
                bb = float(lvv.get("b", 0.0) or 0.0); ss = float(lvv.get("s", 0.0) or 0.0)
                if p >= mid:
                    ub += bb; us += ss
                else:
                    lb += bb; ls += ss
            b["dup"] = (ub - us) / cv * 100.0; b["dlo"] = (lb - ls) / cv * 100.0
            # ½dom = each half's buy/sell COMPOSITION (share of that half), the terminal's "½dom ↑ / ↓" row.
            # Stored as the BUY share; the pane prints "N%B" when >50 else "N%S" with N = 100-buyshare.
            _tu = ub + us; _tl = lb + ls
            b["dom_up_bs"] = (ub / _tu * 100.0) if _tu > 0 else None
            b["dom_lo_bs"] = (lb / _tl * 100.0) if _tl > 0 else None
        else:
            b["dup"] = b["dlo"] = None
            b["dom_up_bs"] = b["dom_lo_bs"] = None
    return A, first


def v11_sigs(A, first):
    """FROZEN v1.1 signals incl. the 2026-07-24 delta freeze (LONG 0<delta<=15 / SHORT delta<0)."""
    out = []
    for i in range(first, len(A) - 1):
        b = A[i]
        if b.get("sk") is None:
            continue
        d = b["delta"]
        if b["up"] and b["sk"] > 0 and b["spread"] >= 35 and 0.0 < d <= 15.0:
            s = 1
        elif b["dn"] and b["sk"] < 0 and b["spread"] <= -35 and d < 0.0:
            s = -1
        else:
            continue
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), b=b))
    return out


# ---------------------------------------------------------------- predicates
def evaluable(sg):
    return sg["b"]["d2pct"] is not None and sg["b"]["p2"] is not None


def f_both(sg):                     # THE PROPOSAL
    b = sg["b"]; s = sg["side"]
    return (b["d2pct"] > 0 and b["p2"] > 0) if s > 0 else (b["d2pct"] < 0 and b["p2"] < 0)


def f_d2(sg):                       # second-half DELTA only
    b = sg["b"]; s = sg["side"]
    return b["d2pct"] > 0 if s > 0 else b["d2pct"] < 0


def f_p2(sg):                       # second-half PRICE only
    b = sg["b"]; s = sg["side"]
    return b["p2"] > 0 if s > 0 else b["p2"] < 0


def f_opposite(sg):                 # CONTROL: the complement of the proposal
    return not f_both(sg)


def f_vert(sg):                     # SECONDARY (screenshot): vertical Δ↑/Δ↓ agreement
    b = sg["b"]; s = sg["side"]
    if b["dup"] is None:
        return False
    return (b["dup"] > 0 and b["dlo"] > 0) if s > 0 else (b["dup"] < 0 and b["dlo"] < 0)


# ---------------------------------------------------------------- engine
def taken(A, sigs, rr, pred=None):
    """One-at-a-time non-overlap chain; `pred` drops a signal BEFORE it claims the slot (live-realistic)."""
    last = -1; out = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        if pred is not None and not pred(sg):
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        out.append(dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"]))
        last = res[2]
    return sorted(out, key=lambda z: z["t"])


def binom_ge(k, n, p):
    if n == 0:
        return float("nan")
    return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return float("nan")
    r1, c1 = a + b, a + c

    def hp(x):
        return (math.comb(c1, x) * math.comb(n - c1, r1 - x)) / math.comb(n, r1)
    po = hp(a); tot = 0.0
    for x in range(max(0, r1 - (n - c1)), min(r1, c1) + 1):
        px = hp(x)
        if px <= po + 1e-12:
            tot += px
    return min(1.0, tot)


def stats(rows):
    n = len(rows); w = sum(1 for r in rows if r["win"])
    net = 1.0
    for r in rows:
        net *= (1 + r["net"])
    return n, w, (100.0 * w / n if n else float("nan")), (net - 1) * 100.0


def line(lbl, rows, be):
    n, w, wr, net = stats(rows)
    p = binom_ge(w, n, be) if n else float("nan")
    return "    %-34s n=%3d win %5.1f%% net %+7.1f%%  P(>=win|BE)=%s" % (
        lbl, n, wr, net, ("%.3f" % p) if n else "--")


def diagnostics(A, first, sigs, ev):
    """Three things that decide whether ANY result below is trustworthy."""
    print("-" * 100)
    print("DIAGNOSTICS")
    print("-" * 100)

    # 1) RECONSTRUCTION VALIDITY. The halves come from a 1m-reconstruction OVERLAY on most buckets (the daemon
    #    only stamps delta_h1 since 2026-07-20 / price_h1 since 2026-07-22). The same reconstruction machinery
    #    carried the da2 bug, so: where BOTH the daemon field and the overlay exist, do they agree?
    ov = archive._load_overlay()
    dd = []; pp = []
    for b in A[first:]:
        pair = ov.get("1h|%.3f" % float(b.get("start_time", 0.0) or 0.0))
        if not pair:
            continue
        # b already merged; compare only where the DAEMON value was present pre-merge is impossible post-hoc,
        # so re-read the raw daemon fields from the untouched source instead.
        pass
    from study.archive_loader import load_archive
    _, raws, _ = load_archive("1h")
    for r in raws:
        pair = ov.get("1h|%.3f" % float(r.get("start_time", 0.0) or 0.0))
        if not pair:
            continue
        if r.get("delta_h1") is not None and pair[0] is not None:
            dd.append((float(r["delta_h1"]), float(pair[0])))
        if r.get("price_h1") is not None and pair[1] is not None:
            pp.append((float(r["price_h1"]), float(pair[1])))

    def agree(pairs, nm, tol):
        if not pairs:
            print("  %s: NO overlap between daemon field and overlay -> reconstruction UNVERIFIABLE here" % nm)
            return
        rel = [abs(a - b) / (abs(a) + 1e-9) for a, b in pairs]
        exact = sum(1 for a, b in pairs if abs(a - b) <= tol * (abs(a) + 1e-9))
        sign = sum(1 for a, b in pairs if (a > 0) == (b > 0))
        print("  %s: n=%d overlap | within %.0f%%: %d (%.0f%%) | SAME SIGN: %d (%.0f%%) | median rel.err %.4f"
              % (nm, len(pairs), tol * 100, exact, 100.0 * exact / len(pairs),
                 sign, 100.0 * sign / len(pairs), sorted(rel)[len(rel) // 2]))
    agree(dd, "delta_h1 daemon-vs-overlay", 0.02)
    agree(pp, "price_h1 daemon-vs-overlay", 0.001)

    # 2) MECHANICAL IMPLICATION. If p2>0 is ~automatic for a bullish bucket, the 'filter' is a restatement of the
    #    base gate, not new information.
    for sd, nm in ((1, "LONG"), (-1, "SHORT")):
        ss = [s for s in ev if s["side"] == sd]
        if not ss:
            continue
        print("  %s evaluable n=%d -> passes d2 %.0f%% | passes p2 %.0f%% | passes BOTH %.0f%%"
              % (nm, len(ss), 100.0 * sum(f_d2(s) for s in ss) / len(ss),
                 100.0 * sum(f_p2(s) for s in ss) / len(ss), 100.0 * sum(f_both(s) for s in ss) / len(ss)))

    # 3) how correlated are the two legs (is d2 adding anything to p2 at all?)
    both_ = sum(1 for s in ev if f_d2(s) and f_p2(s)); only_d = sum(1 for s in ev if f_d2(s) and not f_p2(s))
    only_p = sum(1 for s in ev if f_p2(s) and not f_d2(s)); neither = sum(1 for s in ev if not f_d2(s) and not f_p2(s))
    print("  leg overlap on evaluable: both=%d  d2-only=%d  p2-only=%d  neither=%d" % (both_, only_d, only_p, neither))
    print()


def partition_test(A, ev, rr, be, pred, lbl):
    """CLEAN disjoint test: take ONE chain (the evaluable baseline) and split its trades by the predicate.
    Separate re-linked chains are NOT a partition (their n's don't sum), so Fisher on those is invalid."""
    last = -1; passes = []; fails = []
    for sg in ev:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        row = dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"])
        (passes if pred(sg) else fails).append(row); last = res[2]
    np_, wp, wrp, netp = stats(passes); nf, wf, wrf, netf = stats(fails)
    print("    [%s] WITHIN-CHAIN partition (n=%d = %d pass + %d fail):" % (lbl, np_ + nf, np_, nf))
    print("      pass: n=%3d win %5.1f%% net %+7.1f%%  P(>=win|BE)=%s"
          % (np_, wrp, netp, ("%.3f" % binom_ge(wp, np_, be)) if np_ else "--"))
    print("      fail: n=%3d win %5.1f%% net %+7.1f%%  P(>=win|BE)=%s"
          % (nf, wrf, netf, ("%.3f" % binom_ge(wf, nf, be)) if nf else "--"))
    print("      Fisher pass-vs-fail: p=%.3f" % fisher(wp, np_ - wp, wf, nf - wf))
    # split-half of the PASS set (order-preserved)
    if np_ >= 6:
        mid = np_ // 2
        for hl, sub in (("H1", passes[:mid]), ("H2", passes[mid:])):
            n2, w2, wr2, net2 = stats(sub)
            print("      pass %s: n=%2d win %5.1f%% net %+6.1f%%" % (hl, n2, wr2, net2))


def main():
    A, first = build()
    sigs = v11_sigs(A, first)
    ev = [s for s in sigs if evaluable(s)]
    L = [s for s in sigs if s["side"] > 0]; S = [s for s in sigs if s["side"] < 0]
    print("FROZEN v1.1 signals (with the 2026-07-24 delta freeze): %d  (%dL / %dS)" % (len(sigs), len(L), len(S)))
    print("  EVALUABLE (bucket carries delta_h1 AND price_h1): %d  (%.0f%%) — %d signals cannot be judged\n"
          % (len(ev), 100.0 * len(ev) / max(1, len(sigs)), len(sigs) - len(ev)))
    diagnostics(A, first, sigs, ev)

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 100)
        print("RR 1:%.1f    break-even win = %.0f%%" % (rr, be * 100))
        print("=" * 100)

        print("  LIKE-FOR-LIKE (all chains restricted to the EVALUABLE subset):")
        print(line("baseline (no 2nd-half filter)", taken(A, ev, rr), be))
        print(line(">>> PROPOSAL  d2 AND p2 agree", taken(A, ev, rr, f_both), be))
        print(line("    d2 only (2nd-half delta)", taken(A, ev, rr, f_d2), be))
        print(line("    p2 only (2nd-half price)", taken(A, ev, rr, f_p2), be))
        print(line("    CONTROL: opposite (complement)", taken(A, ev, rr, f_opposite), be))
        print(line("    SECONDARY: vertical dUP/dLO", taken(A, ev, rr, f_vert), be))

        # CLEAN statistical test — a within-chain partition (the rows above are separately RE-LINKED chains,
        # so their n's don't sum and a Fisher across them would double-count).
        partition_test(A, ev, rr, be, f_both, "PROPOSAL d2&p2")
        partition_test(A, ev, rr, be, f_p2, "p2 alone")
        partition_test(A, ev, rr, be, f_d2, "d2 alone")

        # per side (the two sides behave very differently in this family)
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            bl = [r for r in taken(A, ev, rr) if r["side"] == sd]
            pr = [r for r in taken(A, ev, rr, f_both) if r["side"] == sd]
            nb, _, wb, netb = stats(bl); np_, _, wp, netp = stats(pr)
            print("    %s: baseline n=%2d win %5.1f%% net %+6.1f%%  ->  proposal n=%2d win %5.1f%% net %+6.1f%%"
                  % (nm, nb, wb, netb, np_, wp, netp))
        print("  REFERENCE (unrestricted, for context only — NOT comparable to the rows above):")
        print(line("all frozen v1.1 signals", taken(A, sigs, rr), be))
        print()


if __name__ == "__main__":
    main()
