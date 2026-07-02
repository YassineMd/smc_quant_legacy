"""Shared SEL16 scoring core — imported by BOTH the bundle generator (study side) and the terminal's
app/live_score.py, so live pred% and the reference are bit-identical by construction.

SEL16 = features computable on a strict 16-bucket frame [b-15, b]: B-* (selection-native panels), C.*
(15-bucket context), G primitives, and raw entry-bucket E scalars (E*.01 of entry-only base fields).
Everything needing >16 lookback (trailing-30/240 transforms, day-ranks, K.* KC/POC) is excluded at bundle
time. No re-implementations — reuses features.py / features_b.py verbatim.
"""
from __future__ import annotations
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import features as FT
import features_b as FT_B

# base fields whose value is a function of the entry bucket alone (no trailing window) -> E*.01 is SEL16-safe
ENTRY_SAFE_BASE = {"E%02d" % k for k in
                   (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
                    25, 28, 29, 42, 43, 45, 47, 48, 49, 50, 51, 52)}   # E32 target_vol excluded: not in the
#                                                                        live pulse snapshot (would be DEFAULT)


def is_sel16(code):
    """True if a W-STAT feature is computable on a 16-frame (so it belongs in the SEL16 bundle)."""
    if code.startswith(("B-", "C.")):
        return True
    if code.startswith("G") and code[1:2].isdigit():
        return True
    import re
    m = re.match(r"^(E\d+)\.01$", code)          # ONLY the raw (.01) of an entry-only base
    return bool(m) and m.group(1) in ENTRY_SAFE_BASE


from app import config as _cfg   # noqa: E402
LARGE_BINS = [k for k in range(_cfg.SIZE_HIST_NBINS)
              if (0.0 if k == 0 else _cfg.SIZE_HIST_EDGES[k - 1]) >= 273.0]   # [15..20], frozen 273c


def large_net_entry(bk):
    """E52.01 large-order net side APPROX(fixed-273c) on the ENTRY bucket only. None if sz absent."""
    vb = getattr(bk, "sz_vb", []) or []; vs = getattr(bk, "sz_vs", []) or []
    if (sum(vb) + sum(vs)) <= 0:
        return None                              # sz age-mask (pre 2026-06-30)
    lb = sum(vb[k] for k in LARGE_BINS); ls = sum(vs[k] for k in LARGE_BINS)
    return "none" if (lb + ls) == 0 else ("large-buy" if lb >= ls else "large-sell")


def e01_value(base, code, i, kind):
    parent = "E" + code[1:].split(".")[0]
    series = base.get(parent, (None, None))[0]
    if series is None:
        return None
    v = series[i]
    if kind == "sign":
        return 0 if v is None else (1 if v > 0 else (-1 if v < 0 else 0))
    if kind == "log":
        return FT.signed_log(v) if v is not None else None
    return v                                     # raw


def sel16_features(snaps, bks, feat_kinds):
    """{code: value} for the SEL16 features, computed on the 16-frame (i = last bucket). feat_kinds maps
    each code to 'native' (B/C/G) or the E .01 transform kind (raw/sign/log)."""
    n = len(snaps); i = n - 1
    rs = FT.repo_series(snaps, bks)
    base = FT.base_series(snaps, bks, rs)
    times = [float(s.get("end_time", 0.0)) for s in snaps]
    B = FT_B.compute_bscope(snaps, rs, i)
    C = FT.build_context(snaps, rs, i)
    G = FT.build_g(base, rs, times, i)
    out = {}
    for code, kind in feat_kinds.items():
        if code == "E52.01":
            out[code] = large_net_entry(bks[i])
        elif code in B:
            out[code] = B[code]
        elif code in C:
            out[code] = C[code]
        elif code in G:
            out[code] = G[code]
        elif code.startswith("E"):
            out[code] = e01_value(base, code, i, kind)
        else:
            out[code] = None
    return out


def bin_of(v, fi):
    if v is None:
        return None
    if fi["kind"] == "num":
        edges = fi["edges"]
        for k in range(len(edges) - 1):
            lo, hi = edges[k], edges[k + 1]
            if (v >= lo or k == 0) and (v <= hi if k == len(edges) - 2 else v < hi):
                return "Q%d" % (k + 1)
        return None
    return str(v) if str(v) in fi["cats"] else None


def score_side(feat, side_bundle):
    """pred% for one side; None (=> NaN / line gap) if no feature contributed. Per-row null-renorm."""
    base = side_bundle["baseline"]; num = 0.0; den = 0.0
    for code, fi in side_bundle["features"].items():
        b = bin_of(feat.get(code), fi["bin"])
        if b is None:
            continue
        n = fi["bin"]["n"].get(b); tpr = fi["bin"]["tpr"].get(b)
        if n is None or n < 100 or tpr is None:
            continue
        num += fi["weight"] * (tpr - base); den += fi["weight"]
    return (base + num / den) if den > 0 else None
