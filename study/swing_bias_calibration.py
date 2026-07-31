"""Confidence-calibration study for swing_lvn_detect.bias().

CAUSAL: at each sampled bar t we compute bias() over the trailing W buckets (WITH footprints, like the live
terminal's finite window), then measure the FORWARD H-bar directional outcome (does price move in the bias
direction?). We bucket the outcomes by the confidence score and check whether higher-confidence buckets actually
win more — if a 70% bucket doesn't beat a 40% bucket, the confidence is decoration.

Non-overlapping horizons (sample every H bars) so the outcomes are independent. Gross direction (no fee — this
tests prediction, not tradeable PnL). Reports: base directional win%, per-confidence-bin win% + mean forward
return, AUC (does confidence rank wins above losses?), and a label-shuffle null.
"""
import gzip, json, glob, os, sys, time, statistics, bisect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import swing_lvn_detect as S

NCHUNKS = 6           # contiguous 5m chunks (bids 1..NCHUNKS*10000) loaded WITH footprints
W = 800               # trailing window bias() sees (~ the live terminal's 5m window)
HORIZONS = (24, 48)   # forward bars to measure the directional outcome over
BINS = [(-0.01, 0.30), (0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01)]


def load_contiguous(nchunks):
    """The first nchunks*10000 bids of 5m recon WITH footprints, chronological (bid-sorted). Verifies contiguity."""
    root = os.path.join(ROOT, "study", "recon_archive", "5m")
    want = set(range(1, nchunks * 10000 + 1))
    by_bid = {}
    for fn in sorted(glob.glob(os.path.join(root, "5m_*.jsonl.gz"))):
        # parse the chunk's bid range from the filename to skip files we don't need
        base = os.path.basename(fn)[3:-9]
        try:
            lo, hi = (int(x) for x in base.split("_"))
        except ValueError:
            continue
        if lo > nchunks * 10000:
            continue
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line); bid = int(r["bid"])
                if bid in want:
                    by_bid[bid] = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
    bids = sorted(by_bid)
    gaps = sum(1 for a, b in zip(bids, bids[1:]) if b != a + 1)
    return [by_bid[b] for b in bids], gaps


def main():
    t0 = time.time()
    print("loading %d chunks (bids 1..%d) with footprints ..." % (NCHUNKS, NCHUNKS * 10000), flush=True)
    buck, gaps = load_contiguous(NCHUNKS)
    C = [float(b.get("close_price", b.get("close", 0.0)) or 0.0) for b in buck]
    import datetime as dt
    st0 = float(buck[0].get("start_time", 0.0)); st1 = float(buck[-1].get("start_time", 0.0))
    print("  %d buckets, %d gaps, span %s .. %s   (%.0fs)" % (
        len(buck), gaps, dt.datetime.utcfromtimestamp(st0).date(), dt.datetime.utcfromtimestamp(st1).date(), time.time() - t0), flush=True)

    n = len(buck)
    H = max(HORIZONS)
    samples = []          # (conf, state, side, {h: win_bool}, {h: fwd_ret})
    neutral = 0
    for t in range(W, n - H, HORIZONS[0]):                       # every H0 bars -> non-overlapping short horizon
        b = S.bias(buck[t - W:t + 1])
        if not b or b["dir"] is None:
            neutral += 1
            continue
        side = 1 if b["dir"] == "long" else -1
        entry = C[t]
        if entry <= 0:
            continue
        wins = {}; rets = {}
        for h in HORIZONS:
            fr = side * (C[t + h] - entry) / entry
            wins[h] = fr > 0; rets[h] = fr * 100.0
        samples.append((b["confidence"], b["state"], side, wins, rets))
    print("directional samples: %d   (neutral skipped: %d)   (%.0fs)\n" % (len(samples), neutral, time.time() - t0), flush=True)
    if not samples:
        print("no samples"); return

    def report(subset, title):
        if not subset:
            print("  (no samples)"); return
        print("=" * 78); print(title + "   n=%d" % len(subset)); print("=" * 78)
        for h in HORIZONS:
            base_w = sum(1 for s in subset if s[3][h]) / len(subset)
            base_r = statistics.mean(s[4][h] for s in subset)
            print("-- horizon %d bars --  base: win=%.1f%%  mean fwd ret=%+.3f%%" % (h, base_w * 100, base_r))
            print("   %-14s %6s %7s %10s %8s" % ("conf bin", "n", "win%", "mean ret%", "vs base"))
            for lo, hi in BINS:
                rows = [s for s in subset if lo <= s[0] < hi]
                if not rows:
                    continue
                w = sum(1 for s in rows if s[3][h]) / len(rows)
                mr = statistics.mean(s[4][h] for s in rows)
                print("   [%.2f,%.2f) %8d %6.1f%% %9.3f%% %+7.1fpp" % (lo, hi, len(rows), w * 100, mr, (w - base_w) * 100))
            # AUC = P(conf of a win > conf of a loss) via average ranks (Mann-Whitney); ranks depend only on the
            # confidences, so precompute once and reuse for the observed labels + the label-shuffle null.
            labels = [s[3][h] for s in subset]; confs = [s[0] for s in subset]
            order = sorted(range(len(confs)), key=lambda i: confs[i]); ranks = [0.0] * len(confs); i = 0
            while i < len(order):
                j = i
                while j + 1 < len(order) and confs[order[j + 1]] == confs[order[i]]:
                    j += 1
                avg = (i + j) / 2.0 + 1.0
                for k in range(i, j + 1):
                    ranks[order[k]] = avg
                i = j + 1
            nw = sum(1 for l in labels if l); nl = len(labels) - nw
            if nw and nl:
                def _auc(lab):
                    return (sum(ranks[i] for i in range(len(lab)) if lab[i]) - nw * (nw + 1) / 2.0) / (nw * nl)
                auc = _auc(labels)
                import random as _rnd
                _rnd.seed(7); lab2 = labels[:]; aucs = []
                for _ in range(400):
                    _rnd.shuffle(lab2); aucs.append(_auc(lab2))
                aucs.sort(); p025 = aucs[int(0.025 * len(aucs))]; p975 = aucs[int(0.975 * len(aucs))]
                verdict = "REAL (beats null)" if auc > p975 else ("INVERTED" if auc < p025 else "= null (no signal)")
                print("   AUC(confidence ranks wins>losses) = %.3f   null 95%% band [%.3f, %.3f]  -> %s" % (auc, p025, p975, verdict))
            print()

    report(samples, "ALL DIRECTIONAL SIGNALS")
    report([s for s in samples if s[1] == "setup"], "SETUP ONLY (price at the aligned zone)")


if __name__ == "__main__":
    main()
