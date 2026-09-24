# -*- coding: utf-8 -*-
"""AUCTION STATE -- layer 1 of the user's auction-market reading (user 2026-09-24).

The user's doctrine, in their own terms: the market is an auction. Buyers want the cheapest price, sellers the most
expensive; together they build a FAIR VALUE where both are content to trade -- what the HLH Volume Profile finds.
Inside value, sellers are expected to sell ABOVE the POC (it is expensive there) and buyers to lose interest; buyers
are expected to buy BELOW the POC (it is cheap there) and sellers to lose interest. That is RESPONSIVE activity, and
it defends value. When a side stays interested AND keeps its impact where it is supposed to lose it -- sellers below
the POC, buyers above it -- its idea of cheap / expensive has changed: that is INITIATIVE activity, and value is being
re-priced.

This module turns the numbers the terminal already has (the HLH day klines and merged blocs, each cycle's price
range, each side's interest, the leader's impact, the other side's push-back) into that vocabulary, per cycle and
over the recent stretch. Pure numpy, no Qt. DESCRIPTIVE ONLY: it says what the auction is doing now, never what it
will do next.

Two value references (user 2026-09-24: "both"):
  TODAY   the day's own HLH profile (its rows, its 70% value area, the POC at the middle of the busiest run), built
          from the klines that CLOSED BEFORE the cycle started -- the value a participant could see then, never the
          end-of-day profile.
  MERGED  the latest HLH bloc whose candles span 2+ FINISHED days (merges 3 / 4 only ever run on finished days), its
          POC / VAH / VAL -- the established multi-day value, which cannot shift under a cycle while it forms.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import hlh_profile as H

ZONES = ("below value", "lower value", "at POC", "upper value", "above value")
ZONE_BELOW, ZONE_LOWER, ZONE_POC, ZONE_UPPER, ZONE_ABOVE = 0, 1, 2, 3, 4


# ============================================================================ value references
def day_profile(h: np.ndarray, l: np.ndarray, v: np.ndarray, rows: int, va_pct: float):
    """(poc, vah, val) of ONE set of klines, by the HLH period's own rule (hlh_profile.compute_period): rows over the
    klines' range, each kline's volume spread evenly over every row its range touches, the POC at the MIDDLE of the
    busiest run of rows, the value area grown from it (hlh_profile.value_area). None when there is nothing to build."""
    if h.size == 0:
        return None
    hi = float(np.max(h)); lo = float(np.min(l))
    if not hi > lo:
        return None
    step = (hi - lo) / rows
    iT = np.minimum(rows - 1, np.floor((h - lo) / step).astype(np.int64))
    iB = np.minimum(rows - 1, np.maximum(0, np.floor((l - lo) / step).astype(np.int64)))
    iB = np.minimum(iB, iT)
    vp = H._spread(v, iB, iT, rows)
    mx = float(vp.max())
    if not mx > 0:
        return None
    pS = int(np.argmax(vp)); pE = pS
    while pE < rows - 1 and vp[pE + 1] == mx:
        pE += 1
    vaLo, vaHi = H.value_area(vp, 0, rows - 1, va_pct)
    if vaLo < 0:
        return None
    return (lo + (pS + pE + 1) * 0.5 * step, lo + (vaHi + 1) * step, lo + vaLo * step)


class DayValue:
    """TODAY's value as it stood at any moment: the day profile of the klines of that moment's own day that CLOSED
    before it. Memoised per kline range -- a closed kline never changes, so a cycle's value is computed once."""

    def __init__(self, rows: int = 60, va_pct: float = 70.0, tz: str = "UTC", kline_secs: float = 60.0):
        self.rows = int(rows); self.va_pct = float(va_pct); self.tz = tz; self.kline_secs = float(kline_secs)
        self._memo: Dict[tuple, Optional[tuple]] = {}

    def at(self, t: np.ndarray, h: np.ndarray, l: np.ndarray, v: np.ndarray, q_times) -> List[Optional[tuple]]:
        """[(poc, vah, val, minutes)] per query time, None where the day has no closed kline yet."""
        out = []
        t = np.asarray(t, dtype=np.float64)
        for q in np.asarray(q_times, dtype=np.float64).tolist():
            ds = H.period_window(q, False, self.tz)[0]
            i0 = int(np.searchsorted(t, ds, side="left"))
            i1 = int(np.searchsorted(t, q - self.kline_secs, side="right"))
            if i1 <= i0:
                out.append(None)
                continue
            key = (i0, i1, float(t[i0]), float(t[i1 - 1]))
            if key not in self._memo:
                # a zero / NaN-volume kline is not in the HLH period at all (hlh_profile.split_periods): it must not
                # stretch the rows' range either
                hh = np.asarray(h[i0:i1], dtype=np.float64); ll = np.asarray(l[i0:i1], dtype=np.float64)
                vv = np.asarray(v[i0:i1], dtype=np.float64)
                ok = np.isfinite(vv) & (vv > 0)
                r = day_profile(hh[ok], ll[ok], vv[ok], self.rows, self.va_pct)
                # ⚠ bound the memo BEFORE storing: clearing it after the store (as first written) threw away the value
                # just computed and the read below raised KeyError on the 20,001st distinct key (found by the S7 study)
                if len(self._memo) >= 20000:
                    self._memo.clear()
                self._memo[key] = None if r is None else (r[0], r[1], r[2], i1 - i0)
            out.append(self._memo[key])
        return out


def merged_value(finished_rows) -> Optional[dict]:
    """The latest HLH bloc merged across 2+ FINISHED days: {poc, vah, val, tA, tB, name, days}, or None."""
    best = None
    for m in finished_rows or ():
        if m is None or m.dFirst is None or m.dLast is None or not (m.dLast > m.dFirst):
            continue
        if m.vah is None or m.val is None or m.tB is None:
            continue
        if best is None or float(m.tB) > float(best.tB):
            best = m
    if best is None:
        return None
    poc = H.bloc_poc(best)
    return {"poc": float(poc) if poc is not None else None, "vah": float(best.vah), "val": float(best.val),
            "tA": float(best.tA) if best.tA is not None else None, "tB": float(best.tB),
            "name": str(best.name), "days": int(best.dLast - best.dFirst + 1)}


def blocs_value(rows) -> List[dict]:
    """A period's HLH blocs (the D areas and their merges) as plain numbers, oldest first: what the snapshot hands
    a reader so it sees the day's STRUCTURE, not only its one profile."""
    out = []
    for m in rows or ():
        if m is None or m.vah is None or m.val is None:
            continue
        poc = H.bloc_poc(m)
        out.append({"name": str(m.name), "poc": float(poc) if poc is not None else None,
                    "vah": float(m.vah), "val": float(m.val),
                    # its candles' lowest low / highest high, and the OUTER value area (HLH_VA2_PCT) drawn dashed
                    "low": float(m.bLo) if m.bLo is not None else None,
                    "high": float(m.bHi) if m.bHi is not None else None,
                    "val_outer": float(m.val2) if m.val2 is not None else None,
                    "vah_outer": float(m.vah2) if m.vah2 is not None else None,
                    "tA": float(m.tA) if m.tA is not None else None, "tB": float(m.tB) if m.tB is not None else None,
                    "merged": bool(m.merged), "days": (int(m.dLast - m.dFirst + 1)
                                                       if (m.dFirst is not None and m.dLast is not None) else 1),
                    "tag": str(m.tag or "")})
    out.sort(key=lambda b: (b["tA"] if b["tA"] is not None else 0.0))
    return out


# ============================================================================ location and side reading
def zone_of(p: float, poc: Optional[float], vah: Optional[float], val: Optional[float], tick: float,
            at_poc_ticks: float = 1.0) -> Optional[int]:
    """Where a price sits against one value reference. None without a reference."""
    if poc is None or vah is None or val is None or not all(map(math.isfinite, (p, poc, vah, val))):
        return None
    if p > vah:
        return ZONE_ABOVE
    if p < val:
        return ZONE_BELOW
    if abs(p - poc) <= at_poc_ticks * tick + 1e-12:
        return ZONE_POC
    return ZONE_UPPER if p > poc else ZONE_LOWER


def side_label(side_buy: bool, zone: Optional[int], active: bool, effective: bool) -> str:
    """One side's activity in the auction's words: quiet / responsive / initiative / at value, + effective, absorbed
    or passive.

    RESPONSIVE = where the doctrine expects that side (sellers above the POC, buyers below it) -- value defended.
    INITIATIVE = where it is supposed to lose interest (sellers below the POC, buyers above it) -- value re-priced.
    EFFECTIVE  = active (its aggression at or above its own normal) and it moved price its way.
    ABSORBED   = active, but it did not move price its way.
    PASSIVE    = it moved price its way WITHOUT extra aggression: resting orders took the other side's push and
                 handed it back, or price gave way on its own -- not quiet, even though its tape was."""
    if not active and not effective:
        return "quiet"
    how = ("effective" if effective else "absorbed") if active else "passive"
    if zone is None:
        return "active %s" % how
    if zone == ZONE_POC:
        return "at value %s" % how
    expected = (zone in (ZONE_BELOW, ZONE_LOWER)) if side_buy else (zone in (ZONE_ABOVE, ZONE_UPPER))
    return ("responsive %s" if expected else "initiative %s") % how


def verdict(buy_lbl: str, sell_lbl: str, lead: int = 0) -> str:
    """The cycle in one phrase, the sharpest reading first: a CONTEST (both sides moved price their way -- the
    leader converted and the other side pushed it back -- at least one of them aggressively; the leader is named
    first), initiative that worked, initiative absorbed, responsive that worked or absorbed, rotation at value, a
    passive side, quiet."""
    def _moved(lbl):
        return lbl.endswith(" effective") or lbl.endswith(" passive")

    def _kind(lbl):
        k_, how_ = lbl.rsplit(" ", 1)
        return k_ + (" (passive)" if how_ == "passive" else "")
    if _moved(buy_lbl) and _moved(sell_lbl) and (buy_lbl.endswith(" effective") or sell_lbl.endswith(" effective")):
        a_, b_ = (("sellers", sell_lbl), ("buyers", buy_lbl)) if lead < 0 else (("buyers", buy_lbl), ("sellers", sell_lbl))
        return "Contested: %s %s, %s %s" % (a_[0], _kind(a_[1]), b_[0], _kind(b_[1]))
    for kind in ("initiative effective", "initiative absorbed", "responsive effective", "responsive absorbed",
                 "at value effective", "at value absorbed", "active effective", "active absorbed",
                 "initiative passive", "responsive passive", "at value passive", "active passive"):
        b = buy_lbl == kind; s = sell_lbl == kind
        if b or s:
            who = "Buyers and sellers" if (b and s) else ("Buyers" if b else "Sellers")
            olbl = None if (b and s) else (sell_lbl if b else buy_lbl)
            # absorbed BY the other side only when that side moved price its way; else by the book
            other = ("sellers" if b else "buyers") if (olbl and _moved(olbl)) else "the book"
            if kind == "initiative effective":
                return "%s initiative, effective: value being re-priced" % who
            if kind == "initiative absorbed":
                return "%s initiative, absorbed by %s: value defended" % (who, other)
            if kind == "responsive effective":
                return "%s responsive, effective: value defended" % who
            if kind == "responsive absorbed":
                return "%s responsive, absorbed by %s" % (who, other)
            if kind.startswith("at value"):
                return "Rotation at the POC"
            if kind.endswith(" passive"):
                return "%s %s, passive: price moved their way without extra aggression" % (who, kind[:-len(" passive")])
            return "%s active" % who
    return "Quiet: neither side above its normal"


def classify(price_mid, refs_today, ref_merged, interest_b, interest_s, lead, good, pb, give, tick,
             active_min: float = 1.0, give_min: float = 4.0) -> List[dict]:
    """Per cycle: the location against both references and each side's label. Arrays aligned with the cycles.

    A side is ACTIVE when its aggressive $/s ran at or above its own normal (interest >= active_min). It is EFFECTIVE
    when it LED and its push converted (the I x I fill; a short push never does), or when it did not lead and pushed
    price back from the leader's extreme by at least give_min ticks at or above its own usual push-back."""
    out = []
    n = len(price_mid)
    for k in range(n):
        p = float(price_mid[k])
        rt = refs_today[k] if refs_today is not None else None
        zt = zone_of(p, rt[0], rt[1], rt[2], tick) if rt else None
        zm = zone_of(p, ref_merged["poc"], ref_merged["vah"], ref_merged["val"], tick) if ref_merged else None
        ib = float(interest_b[k]); is_ = float(interest_s[k])
        ld = int(lead[k])
        pbk = float(pb[k]); gv = float(give[k])
        pushed_back = math.isfinite(pbk) and pbk >= 1.0 and math.isfinite(gv) and gv >= give_min
        eff_b = (ld > 0 and bool(good[k])) or (ld < 0 and pushed_back)
        eff_s = (ld < 0 and bool(good[k])) or (ld > 0 and pushed_back)
        act_b = math.isfinite(ib) and ib >= active_min
        act_s = math.isfinite(is_) and is_ >= active_min
        lb = side_label(True, zt, act_b, eff_b) if ld != 0 else "unrated"
        ls = side_label(False, zt, act_s, eff_s) if ld != 0 else "unrated"
        out.append({"zone": zt, "mzone": zm,
                    "dist": ((p - rt[0]) / tick) if rt else float("nan"),
                    "mdist": ((p - ref_merged["poc"]) / tick) if (ref_merged and ref_merged.get("poc") is not None) else float("nan"),
                    "buy": lb, "sell": ls,
                    "verdict": verdict(lb, ls, ld) if ld != 0 else ""})
    return out


def summary(rows: List[dict], t0, t_end, now: float, last_n: int = 12, last_secs: float = 3600.0) -> dict:
    """The recent stretch: per side, how many of the last `last_n` finished rated cycles were initiative / responsive,
    effective / absorbed; and the share of the last hour's time spent in each zone of today's value."""
    idx = [k for k in range(len(rows)) if rows[k].get("buy") not in (None, "unrated")]
    tail = idx[-int(last_n):]
    cnt = {"buy": {}, "sell": {}}
    for k in tail:
        for s in ("buy", "sell"):
            cnt[s][rows[k][s]] = cnt[s].get(rows[k][s], 0) + 1
    share = {z: 0.0 for z in ZONES}
    tot = 0.0
    for k in range(len(rows)):
        a = max(float(t0[k]), now - last_secs); b = min(float(t_end[k]), now)
        if b <= a or rows[k].get("zone") is None:
            continue
        share[ZONES[rows[k]["zone"]]] += b - a
        tot += b - a
    if tot > 0:
        share = {z: round(100.0 * s / tot, 1) for z, s in share.items()}
    return {"last_n": len(tail), "buyers": cnt["buy"], "sellers": cnt["sell"], "time_share_pct_last_hour": share}
