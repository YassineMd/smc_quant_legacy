"""Interpretation pane: name what happened in each cycle, one row per cycle, newest first.

The user's state table (2026-09-11) has seven states across five columns -- buy vol, sell vol, bid book, ask
book, price. The states come from the QUADRANT MAP at the top of that same picture, which is the part that
survives contact with live tape:

        aggressive volume x price displacement

        light + big move   VACUUM        book pulled away, price gaps on little flow
        heavy + big move   BREAKOUT      one side overwhelms, price reprices
        heavy + small      ABSORPTION    heavy flow, price holds -- someone is refilling
        light + small      QUIET         nobody transacting

MEASURED FIRST on 20 h of live tape, n=670 cycles (669 finished), before any of this was drawn:

  * Aggressive volume is $ PER SECOND, never total $. Total-$ against the previous cycles shares 49% (buy) and
    53% (sell) of its variance with cycle DURATION -- a long cycle would read "heavy" on both sides at once and
    every long cycle would drift into breakout/absorption purely by being long. The rate drops that to 2-3%.
    This is the same trap the Volume pane fell into; it is checked now rather than discovered later.
  * Price displacement is the Speed pane's own ratio -- |ticks/s| against the same side's previous 5 -- so the
    two panes can never disagree about whether a cycle moved fast.
  * Splitting each axis at its own baseline (ratio > 1.0) populates the four quadrants 28 / 22 / 21 / 29%, and
    the seven named states 9.5-14.4% each. No cell is degenerate and nothing is forced.
  * CONFIDENCE is real and reported: the median cycle sits 0.49 log2 units from the crosshair, but 36% sit
    inside 0.35. Those are drawn DIM. A cycle that is 2% heavier and 2% faster than its baseline is not a
    breakout and the pane must not present it as one.

⚠ What did NOT reproduce, stated plainly: the table's full five-cell signature. Matching each labelled cycle
against its row's own (buy, sell, bid, ask) pattern scored 1.00 of 4 cells, where chance alone gives ~1.33 --
and that check had only ~42 usable cycles, so it is weak evidence either way. Either way it is not evidence
FOR the pattern. So the book and per-side volume are shown as EVIDENCE next to the state, never as inputs to
it. The two classifier axes are the ones that were measured to work.

⚠ Book coverage depends on ZOOM. A cycle needs LOB_MIN_COLS depth columns inside it; over a 24 h view the
liquidity window's columns are wider than a 77 s cycle, so only ~26% of cycles get a book reading. Zoomed in,
nearly all do. A missing reading prints "-", never a fabricated one.
"""
from __future__ import annotations

import bisect
import math
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

# The four states carry the user's own palette from the state-space picture: green absorption, amber breakout,
# red vacuum, grey quiet.
ST_ABSORB, ST_BREAK, ST_VACUUM, ST_QUIET, ST_FORMING = 0, 1, 2, 3, 4
STATE_NAME = ("ABSORPTION", "BREAKOUT", "VACUUM", "QUIET", "forming")


def state_label(st, side):
    """What the row says. ABSORPTION is phrased as BUYER/SELLER ABSORBED (user 2026-09-11) because that is
    what it always meant: the side is the one that was AGGRESSING and got absorbed, so "ABSORPTION buy" read
    as if buyers were doing the absorbing. The other states name the direction price went, so they keep the
    NAME + side form."""
    if st == ST_ABSORB:
        return ("BUYER ABSORBED" if side == "buy" else "SELLER ABSORBED")
    return STATE_NAME[st] + ((" " + side) if side else "")

# COLOUR is keyed on state AND side, because BREAKOUT is the aggressive state and the user wants that legible
# at a glance (2026-09-11): buy vivid green, sell vivid red, instead of the state-space picture's single
# amber. ABSORPTION splits by side too (user 2026-09-12): BUYER ABSORBED orange, SELLER ABSORBED blue -- the
# two are opposite readings of the tape and sharing one colour made them one thing at a glance.
#   ⚠ C_ABSORB_SELL is APPENDED, not inserted: these are indices into three parallel tuples and into rows
#   already built, so inserting in the middle would silently recolour every other state.
#   BLUE MEASURED, not picked: #2979FF's nearest neighbour in this palette is 192 channel-units away (from
#   quiet/gray), where the closest EXISTING pair -- quiet vs forming -- is only 89 apart.
C_ABSORB_BUY, C_BREAK_BUY, C_BREAK_SELL, C_VACUUM, C_QUIET, C_FORMING, C_ABSORB_SELL = 0, 1, 2, 3, 4, 5, 6
# A BREAKOUT AGAINST ITS LEADER (user 2026-09-23: "they are breakout bars and have orange histogram bar on
# interestximpact"): the side whose aggressive $/s ran further above its own normal is NOT the way price broke --
# the INTEREST x IMPACT pane's own orange "contra" rule (iimp_contra below). Breakout and absorption at once: the
# leader was absorbed and price broke the other way. Bright green = broke UP through sellers, bright purple =
# broke DOWN through buyers (the user's colours). APPENDED like C_ABSORB_SELL; they are still BREAKOUTS to every
# rule that asks (BREAK_BUY_COLS / BREAK_SELL_COLS).
C_BREAK_BUY_X, C_BREAK_SELL_X = 7, 8
BREAK_BUY_COLS = (C_BREAK_BUY, C_BREAK_BUY_X)
BREAK_SELL_COLS = (C_BREAK_SELL, C_BREAK_SELL_X)
C_ABSORB = C_ABSORB_BUY                     # kept for anything still importing the old name
BAR_COL = ("#FF9500", "#00C853", "#FF1F1F", "#E2574C", "#6B7A82", "#4E5C64", "#2979FF", "#76FF03", "#D500F9")
# ... and TEXT is per THEME. It was not: on the white Simple BW ground every name drew in a pale dark-theme
# colour and was barely readable.
TXT_DARK = ("#FFB84D", "#2BE86B", "#FF5A5A", "#F0857C", "#9AAAB2", "#6B7A82", "#7FB2FF", "#9CFF57", "#E57BFF")
TXT_LIGHT = ("#A85C00", "#00822F", "#C40D0D", "#A8382F", "#5A666D", "#6B7A82", "#0B4FA8", "#3F7F00", "#8E00B0")

# the price move, coloured by the move itself: green up, red down, grey when it ended where it started
MOVE_DARK = ("#FF5A5A", "#7A828C", "#2BE86B")
MOVE_LIGHT = ("#C40D0D", "#77808A", "#00822F")

# kept for anything still importing the old names
STATE_COL = (BAR_COL[C_ABSORB], BAR_COL[C_BREAK_BUY], BAR_COL[C_VACUUM], BAR_COL[C_QUIET], BAR_COL[C_FORMING])
STATE_TXT = (TXT_DARK[C_ABSORB], TXT_DARK[C_BREAK_BUY], TXT_DARK[C_VACUUM], TXT_DARK[C_QUIET],
             TXT_DARK[C_FORMING])


def iimp_contra(buy_ratio, sell_ratio, move):
    """The INTEREST x IMPACT pane's ORANGE bar, as a rule anyone can ask: the LEADER -- the side whose aggressive $/s
    ran further above its OWN last-N normal (log2 buy/sell >= 0 = buyers) -- is not the way price went. Needs both
    ratios finite and positive (the pane rates nothing else) and a real move; flat is never contra. Vectorised;
    scalars in, a 0-d bool out."""
    b = np.asarray(buy_ratio, dtype=np.float64); s_ = np.asarray(sell_ratio, dtype=np.float64)
    m = np.asarray(move, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(b) & np.isfinite(s_) & (b > 0) & (s_ > 0) & np.isfinite(m)
        lead_buy = b >= s_
        return ok & ((lead_buy & (m < 0)) | (~lead_buy & (m > 0)))


def colour_of(st, side, contra=False):
    """Which colour a row draws in. BREAKOUT and ABSORPTION both split by side; a BREAKOUT against its leader
    (`contra`, see iimp_contra) takes the bright pair.

    This is the ONE place either pane asks, which is why the cycle candles above the flow lines and the rows
    in this feed cannot disagree about what a colour means."""
    if st == ST_BREAK:
        if contra:
            return C_BREAK_BUY_X if side == "buy" else C_BREAK_SELL_X
        return C_BREAK_BUY if side == "buy" else C_BREAK_SELL
    if st == ST_ABSORB:
        return C_ABSORB_BUY if side == "buy" else C_ABSORB_SELL
    return (None, None, C_VACUUM, C_QUIET, C_FORMING)[st]


def speed_word(mv, flat, sr, slow_c, fast_c):
    """How price moved, in the user's own words, on the SPEED PANE's measured cuts.

        flat                 under the flat-tick floor -- the direction means nothing
        drifting up / down   slower than SPEED_SLOW x its own baseline
        up / down            normal
        fast up / down       faster than SPEED_FAST x

    Same thresholds the CYCLE SPEED pane draws its four classes with, so the two can never disagree about one
    cycle. With no baseline yet, it states the direction and claims nothing about the speed."""
    if flat:
        return "flat"
    d = "up" if float(mv) > 0 else "down"
    if not np.isfinite(sr):
        return d
    if sr < float(slow_c):
        return "drifting " + d
    if sr > float(fast_c):
        return "fast " + d
    return d


def absorb_move_text(side_buy, px_open, px_close, px_hi, px_lo, tick, push_min, dec):
    """An absorbed row's price line: `hi 102.45 -> 102.37   -8t   (84% given back)`.

    Returns (line, word, fraction). Deliberately PREFIXED with hi/lo: without it the line looks identical to
    every other row's open-to-close line while meaning something different, which is the kind of thing a
    reader never notices until it has misled them once."""
    push, give, frac = rejection(side_buy, px_open, px_close, px_hi, px_lo, tick, push_min)
    if not np.isfinite(give):
        return "", "", float("nan")
    f = "%%.%df" % int(dec)
    anchor = float(px_hi) if side_buy else float(px_lo)
    t = int(round(give))
    txt = ("hi " if side_buy else "lo ") + (f + " -> " + f + "   %+dt") % (
        anchor, float(px_close), -t if side_buy else t)
    # the percentage goes in the WORD slot beside the state, not onto this line: joined it needs 517 px
    # against a 440 px panel. For an absorbed cycle it is also the better thing to have there -- the movement
    # word is near-redundant, since absorbed REQUIRES a speed at or below baseline.
    # ⚠ above 100% they handed back the whole push AND price closed past the open -- a REVERSAL. Saying so
    # beats printing "167% given back", and it is a real category: 38% of absorbed cycles land there.
    if not np.isfinite(frac):
        word = ""
    elif frac >= 1.0:
        word = "fully reversed"
    else:
        word = "%.0f%% given back" % (frac * 100.0)
    return txt, word, frac


def move_text(px0, px1, mv, flat, sr, slow_c, fast_c, dec):
    """(`100.01 -> 97.30   -271t`, `fast down`, sign).

    Returned as TWO pieces because they are drawn at opposite ends of the line: concatenated, the worst
    realistic case needs 451 px against a 380 px panel and would clip. The sign is taken from the ROUNDED tick
    count -- the same number printed -- so a row can never show "+0t" in a colour that claims a direction.
    That is the rule the cycle badges already follow."""
    t = int(round(float(mv))) if np.isfinite(mv) else 0
    sign = 0 if t == 0 else (1 if t > 0 else -1)
    spd = speed_word(mv, flat, sr, slow_c, fast_c)
    if not (np.isfinite(px0) and np.isfinite(px1)):
        return "%+dt" % t, spd, sign
    f = "%%.%df" % int(dec)
    return (f + " -> " + f + "   %+dt") % (px0, px1, t), spd, sign


def dur_text(secs: float) -> str:
    """15s / 1m5s / 1h2m -- the user's format."""
    s = int(round(max(0.0, float(secs))))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        m, r = divmod(s, 60)
        return "%dm%ds" % (m, r) if r else "%dm" % m
    h, r = divmod(s, 3600)
    m = r // 60
    return "%dh%dm" % (h, m) if m else "%dh" % h


def _clock(ts: float) -> str:
    lt = time.localtime(float(ts))
    return "%02d:%02d:%02d" % (lt.tm_hour, lt.tm_min, lt.tm_sec)


def _at(arr, k) -> float:
    """A column the caller did not supply reads as missing, never as zero."""
    if arr is None:
        return float("nan")
    try:
        return float(arr[k])
    except Exception:
        return float("nan")


def _ratio_text(r: float) -> str:
    if not np.isfinite(r):
        return "-"
    return ("%.2f" % r) if r < 10 else ("%.0f" % r)


def _median_prev_windows(hv: np.ndarray, nb: int, mn: int) -> np.ndarray:
    """base[j] = median of hv[max(0, j - nb):j] for j = 0..len(hv) (the history BEFORE the j-th append),
    NaN while fewer than mn values exist.

    ONE SORTED WALK (2026-09-20): each value is inserted with bisect.insort and the one that leaves the window
    removed the same way -- both C-level moves -- and every median is a middle pick with np.median's own
    arithmetic (the mean of the two middles when the count is even), so the numbers are bit-identical to the
    medians this replaced (the gates hold it to the loops). The earlier form vectorised the full windows but
    paid one np.median per ramp step, nb of them on every call: 12 ms at a 200-cycle lookback, per side, per
    frame, on the GUI thread."""
    m = int(hv.shape[0])
    base = np.full(m + 1, np.nan)
    if m < mn:
        return base
    vals = np.asarray(hv, dtype=np.float64).tolist()
    srt: list = []
    ins, left = bisect.insort, bisect.bisect_left
    for j in range(m + 1):
        if j >= mn:
            cnt = len(srt); h = cnt >> 1
            base[j] = srt[h] if (cnt & 1) else 0.5 * (srt[h - 1] + srt[h])
        if j < m:
            ins(srt, vals[j])
            if j >= nb:
                del srt[left(srt, vals[j - nb])]
    return base


def history_depth(ok, dn, groups) -> np.ndarray:
    """How much history each row's ratio stands on: the number of finished + valid values of ITS OWN group
    appended before it -- the very `j` _ratio_vec indexes its median windows with, so it is the one
    definition of the thing and cannot drift from the ratios.

    A ratio built on k values and one built on k + 1 are different numbers until k reaches n_base; past
    that the window is exactly the last n_base and no deeper tape can change it. The PRICE pane's candle
    cache keeps this beside every colour to know whether a later read can still improve the rating
    (2026-09-20: at boot the store backfills newest-first in 2 h chunks while the pane already ticks, so
    the first ratings stood on minutes of tape and, once cached, were never revisited)."""
    ok = np.asarray(ok, dtype=bool)
    dn = np.asarray(dn, dtype=bool)
    groups = np.asarray(groups)
    app = ok & dn
    out = np.zeros(app.shape[0], dtype=np.int64)
    for g in np.unique(groups):
        mg = groups == g
        a = app & mg
        out[mg] = (np.cumsum(a) - a)[mg]                                  # appended BEFORE k, exclusive of k
    return out


def _ok_prev(v):
    """prev_ratio's validity: a positive, finite rate."""
    return np.isfinite(v) & (v > 0)


def _ok_side(v):
    """same_side_ratio's validity: finite and >= 0 -- zero is a legitimate speed (the FLAT class)."""
    return np.isfinite(v) & (v >= 0)


def prev_depth(vals, done) -> np.ndarray:
    """history_depth under prev_ratio's own inputs and validity rule (one group)."""
    v = np.asarray(vals, dtype=np.float64)
    return history_depth(_ok_prev(v), done, np.zeros(v.shape[0], dtype=np.int64))


def same_side_depth(vals, is_dom_buy, done) -> np.ndarray:
    """history_depth under same_side_ratio's own inputs and validity rule (one group per side)."""
    v = np.asarray(vals, dtype=np.float64)
    return history_depth(_ok_side(v), done, np.asarray(is_dom_buy, dtype=bool).astype(np.int64))


def _ratio_vec(v, ok, dn, groups, nb: int, mn: int, include_open: bool) -> np.ndarray:
    """Shared body: per group (one for prev_ratio, one per side for same_side_ratio) the appended history is
    the finished + valid values in order; each rated cycle divides by the median of the previous nb of them."""
    n = int(v.shape[0])
    out = np.full(n, np.nan)
    app = ok & dn
    rated = ok & (dn | bool(include_open))
    j = history_depth(ok, dn, groups)                                     # the one definition of "how much history"
    for g in np.unique(groups):
        mg = groups == g
        hv = v[app & mg]
        base = _median_prev_windows(hv, nb, mn)
        r = rated & mg
        b = base[j[r]]
        with np.errstate(divide="ignore", invalid="ignore"):
            q = np.where(b > 0, v[r] / b, np.nan)
        out[r] = q
    return out


def prev_ratio(vals, done, n_base: int, min_n: int, include_open: bool = False):
    """Each cycle's value over the MEDIAN of the PREVIOUS n_base cycles.
    Not same-side: total aggressive flow and the book both exist in every cycle, so the natural baseline is
    simply what came before -- the Book pane's rule.
    `include_open` rates the cycle STILL FORMING against that same baseline, from what has accumulated so
    far, so the feed can name a state while it is happening (user 2026-09-11). An unfinished cycle is never
    APPENDED to the history whichever way the flag is set: a partial cycle is not a normal, and letting one
    in would drag every later reading toward a half-formed value.
    Vectorised 2026-09-14 (was a Python loop with an np.median per cycle: 28 ms per 400 cycles); the loop
    stays as prev_ratio_loop and a gate holds the two equal."""
    n = int(np.size(vals))
    if n == 0:
        return np.full(0, np.nan)
    v = np.asarray(vals, dtype=np.float64)
    dn = np.asarray(done, dtype=bool)
    ok = _ok_prev(v)
    return _ratio_vec(v, ok, dn, np.zeros(n, dtype=np.int64), max(1, int(n_base)), max(1, int(min_n)), include_open)


def prev_ratio_loop(vals, done, n_base: int, min_n: int, include_open: bool = False):
    """The reference loop (kept for the equality gate)."""
    n = int(np.size(vals))
    out = np.full(n, np.nan)
    if n == 0:
        return out
    v = np.asarray(vals, dtype=np.float64)
    dn = np.asarray(done, dtype=bool)
    hist = []
    nb = max(1, int(n_base)); mn = max(1, int(min_n))
    for k in range(n):
        if not (bool(dn[k]) or include_open):
            continue
        if not (np.isfinite(v[k]) and v[k] > 0):
            continue
        if len(hist) >= mn:
            base = float(np.median(hist[-nb:]))
            if base > 0:
                out[k] = v[k] / base
        if dn[k]:
            hist.append(float(v[k]))
    return out


def same_side_ratio(vals, is_dom_buy, done, n_base: int, min_n: int, include_open: bool = False):
    """The Speed pane's rule -- each cycle against the SAME side's previous n_base -- with the open cycle
    optionally rated too.
    Identical to the terminal's own _same_side_ratio on FINISHED cycles, and a test gate holds the two to
    each other. That includes its `v >= 0` guard: zero is a legitimate speed, and requiring v > 0 silently
    dropped exactly the cycles the FLAT class exists to show. The open cycle enters no side's history.
    Vectorised 2026-09-14; the loop stays as same_side_ratio_loop and a gate holds the two equal."""
    n = int(np.size(done))
    if n == 0:
        return np.full(0, np.nan)
    v = np.asarray(vals, dtype=np.float64)
    dn = np.asarray(done, dtype=bool)
    db = np.asarray(is_dom_buy, dtype=bool).astype(np.int64)
    ok = _ok_side(v)
    return _ratio_vec(v, ok, dn, db, max(1, int(n_base)), max(1, int(min_n)), include_open)


def same_side_ratio_loop(vals, is_dom_buy, done, n_base: int, min_n: int, include_open: bool = False):
    """The reference loop (kept for the equality gate)."""
    n = int(np.size(done))
    out = np.full(n, np.nan)
    if n == 0:
        return out
    v = np.asarray(vals, dtype=np.float64)
    dn = np.asarray(done, dtype=bool)
    db = np.asarray(is_dom_buy, dtype=bool)
    hist = {True: [], False: []}
    nb = max(1, int(n_base)); mn = max(1, int(min_n))
    for k in range(n):
        if not (bool(dn[k]) or include_open):
            continue
        if not (np.isfinite(v[k]) and v[k] >= 0):
            continue
        h = hist[bool(db[k])]
        if len(h) >= mn:
            base = float(np.median(h[-nb:]))
            if base > 0:
                out[k] = v[k] / base
        if dn[k]:
            h.append(float(v[k]))
    return out


def same_side_diff(vals, is_dom_buy, done, n_base: int, min_n: int) -> np.ndarray:
    """Each cycle's value MINUS the median of the SAME side's previous n_base finished values -- the Interest x
    Impact score's rule (a residual judged against its own side's recent residuals). Every finite row is rated,
    the forming one included; only FINISHED rows enter a side's history; NaN below min_n.
    Vectorised 2026-09-20: as a Python loop with an np.median per cycle it ran on every frame of a live session
    and a py-spy sample put it at 17% of the GUI thread. The loop stays as same_side_diff_loop and a gate holds
    the two equal."""
    n = int(np.size(done))
    if n == 0:
        return np.full(0, np.nan)
    v = np.asarray(vals, dtype=np.float64)
    dn = np.asarray(done, dtype=bool)
    groups = np.asarray(is_dom_buy, dtype=bool).astype(np.int64)
    ok = np.isfinite(v)
    nb, mn = max(1, int(n_base)), max(1, int(min_n))
    out = np.full(n, np.nan)
    app = ok & dn
    j = history_depth(ok, dn, groups)
    for g in np.unique(groups):
        mg = groups == g
        base = _median_prev_windows(v[app & mg], nb, mn)
        r = ok & mg
        out[r] = v[r] - base[j[r]]
    return out


def same_side_diff_loop(vals, is_dom_buy, done, n_base: int, min_n: int) -> np.ndarray:
    """The reference loop same_side_diff replaced (kept for the gate)."""
    v = np.asarray(vals, dtype=np.float64)
    out = np.full(int(v.size), np.nan)
    hist = {True: [], False: []}
    for k in range(int(v.size)):
        if not np.isfinite(v[k]):
            continue
        h = hist[bool(is_dom_buy[k])]
        if len(h) >= int(min_n):
            out[k] = float(v[k] - np.median(h[-int(n_base):]))
        if bool(done[k]):
            h.append(float(v[k]))
    return out


def _quadrant(heavy, big, up, dom_buy):
    """The user's quadrant map. Breakout and vacuum name the direction PRICE went; absorption names the side
    doing the AGGRESSING -- the one being absorbed -- which is the cycle's own dominance flag, i.e. exactly
    what the vertical line's colour already shows."""
    if heavy and big:
        return ST_BREAK, ("buy" if up else "sell")
    if heavy:
        return ST_ABSORB, ("buy" if dom_buy else "sell")
    if big:
        return ST_VACUUM, ("buy" if up else "sell")
    return ST_QUIET, ""


def rejection(side_buy, px_open, px_close, px_hi, px_lo, tick, push_min):
    """(push, giveback, fraction) for an absorbed cycle, all in ticks -- the shape of the rejection.

    BUYER ABSORBED: buyers drove price up to the HIGH, so the push is high-open and what they handed back is
    high-close. SELLER ABSORBED mirrors it off the LOW. Both are >= 0 by construction.

    The FRACTION is the strength: 0.5 means half the push was handed back, 1.0 the whole of it, and above 1.0
    price closed PAST where the cycle opened -- a reversal, not merely an absorption. It is scale-free, so
    unlike everything else in this pane it needs no baseline. NaN under a `push_min` push: there is nothing
    to reject, and dividing by it would manufacture a number."""
    if not all(np.isfinite(v) for v in (px_open, px_close, px_hi, px_lo)):
        return float("nan"), float("nan"), float("nan")
    if side_buy:
        push = (float(px_hi) - float(px_open)) / tick
        give = (float(px_hi) - float(px_close)) / tick
    else:
        push = (float(px_open) - float(px_lo)) / tick
        give = (float(px_close) - float(px_lo)) / tick
    push = max(push, 0.0); give = max(give, 0.0)
    frac = give / push if push >= float(push_min) else float("nan")
    return push, give, frac


def _line1(vr_k, buy_ratio, sell_ratio, k):
    return "flow %sx   buy %s  sell %s" % (_ratio_text(vr_k), _ratio_text(_at(buy_ratio, k)),
                                           _ratio_text(_at(sell_ratio, k)))


def _pct_text(r) -> str:
    """A ratio as a DEVIATION from its own baseline: 1.00 -> "0%", 1.02 -> "+2%", 0.94 -> "-6%".

    Easier to read than a multiplier for something that hovers near 1: the book's whole range at the default
    radius is roughly 0.90 to 1.11, which as multipliers all look alike. Exactly 0 prints bare, without a
    "+", because "+0%" claims a direction it does not have."""
    if not np.isfinite(r):
        return "-"
    p = (float(r) - 1.0) * 100.0
    n = int(round(p))
    return "0%" if n == 0 else "%+d%%" % n


def _line2(bid_ratio, ask_ratio, k):
    """The user's last two columns, named for what they ARE (user 2026-09-12): resting bids are limit BUYERS
    waiting to be hit, resting asks are limit SELLERS. "bid"/"ask" invited confusion with the aggressive
    buy/sell figures on the line above, which are a different thing entirely.

    Returned as TWO pieces, drawn at opposite ends of the line. Joined by a dash they need 462 px against a
    404 px panel, and the dash collides with the "-" that means no reading:
    `Limit Buyers -  -  Limit Sellers -`."""
    return ("Limit Buyers %s" % _pct_text(_at(bid_ratio, k)),
            "Limit Sellers %s" % _pct_text(_at(ask_ratio, k)))


def build_rows(t, t_end, done, move, side_dom, vol_ratio, speed_ratio,
               bid_ratio, ask_ratio, buy_ratio, sell_ratio, flat_ticks, weak_below, max_rows,
               now=None, live=True, px_start=None, px_end=None, px_dec=2,
               slow_c=0.65, fast_c=1.50, px_hi=None, px_lo=None,
               tick=0.01, push_min=2.0, reject_weak=0.68):
    """One display row per cycle, NEWEST FIRST. Pure function of arrays -- no Qt, so it is directly testable.

    Every string is built here, once per rebuild, so paintEvent only ever draws pre-made text."""
    n = int(np.size(t))
    if n == 0:
        return []
    mv = np.nan_to_num(np.asarray(move, dtype=np.float64), nan=0.0)
    dn = np.asarray(done, dtype=bool)
    vr = np.asarray(vol_ratio, dtype=np.float64)
    sr = np.asarray(speed_ratio, dtype=np.float64)
    sd = np.asarray(side_dom, dtype=bool)
    flat = np.abs(mv) < float(flat_ticks)
    # each axis is split at its OWN baseline: "heavier than this cycle's own recent normal", "faster than the
    # same side's recent normal". The measured medians were 0.963 and 0.981, i.e. within 4% of 1.0, so the
    # self-describing cut costs nothing and needs no magic number.
    heavy = vr > 1.0
    big = (sr > 1.0) & ~flat
    up = mv > 0
    rateable = np.isfinite(vr) & np.isfinite(sr)
    ok = rateable & dn
    # distance from the crosshair, in log2 units, on whichever axis is the WEAKER of the two -- a cycle only
    # earns a confident label if BOTH axes are clear of their baseline
    with np.errstate(divide="ignore", invalid="ignore"):
        conf = np.minimum(np.abs(np.log2(np.maximum(vr, 1e-9))), np.abs(np.log2(np.maximum(sr, 1e-9))))

    def _raw(k, st, side, dur):
        """The NUMBERS behind a row (2026-09-23 card redesign): the feed now DRAWS them -- the effort x result
        quadrant, the tape bars, the book arrows, the give-back bar -- instead of only printing sentences.
        Appended as the row's 14th element, so every reader of the first 13 is untouched."""
        p0, p1, ph, pl = _at(px_start, k), _at(px_end, k), _at(px_hi, k), _at(px_lo, k)
        push = give = frac = float("nan")
        if st == ST_ABSORB:
            push, give, frac = rejection(side == "buy", p0, p1, ph, pl, float(tick), float(push_min))
        return {"st": int(st), "side": side or "", "mv": float(mv[k]), "flat": bool(flat[k]), "dur": float(dur),
                "px0": p0, "px1": p1, "hi": ph, "lo": pl, "vr": _at(vr, k), "sr": _at(sr, k),
                "buy": _at(buy_ratio, k), "sell": _at(sell_ratio, k), "bid": _at(bid_ratio, k),
                "ask": _at(ask_ratio, k), "push": push, "give": give, "gb": frac}

    def _contra(k):
        """The I x I pane's orange for this row: its tape ratios ARE the pane's interest (same lookback)."""
        return bool(iimp_contra(_at(buy_ratio, k), _at(sell_ratio, k), mv[k]))

    rows = []
    order = range(n - 1, -1, -1)
    for k in order:
        if len(rows) >= int(max_rows):
            break
        t0 = float(t[k]); t1 = float(t_end[k])
        if not dn[k]:
            # ⚠ crosses() marks the LAST cycle of any read unfinished, whether or not that read reached the
            # live edge. Panned into the past, that "forming" cycle finished hours ago AND its volume is
            # truncated at the view's right edge -- so it is dropped, not relabelled. Only when the view
            # actually contains the live edge is the open cycle real.
            if not live:
                continue
            # The RUNNING interpretation (user 2026-09-11): rate what has accumulated SO FAR against the same
            # baselines the finished cycles use, and name the state while it is happening. It is tagged
            # "forming" and never counts as settled -- a cycle that is heavy-and-fast at 30 s can still end
            # heavy-and-flat. t_end arrives clamped to now, so the elapsed here is the real one.
            el = max(0.0, t1 - t0) if t1 > t0 else max(
                0.0, (float(now) if now is not None else time.time()) - t0)
            head = "%s - ... - %s" % (_clock(t0), dur_text(el))
            if not rateable[k]:
                rows.append((t0, t0 + el, head, "forming", "", ("", ""), ST_FORMING, False, False, C_FORMING, "", 0, "",
                             _raw(k, ST_FORMING, "", el)))
                continue
            st, side = _quadrant(heavy[k], big[k], up[k], sd[k])
            _mt, _mw, _ms = move_text(_at(px_start, k), _at(px_end, k), mv[k], flat[k], sr[k],
                                      slow_c, fast_c, px_dec)
            if st == ST_ABSORB:
                _at_, _aw, _fr = absorb_move_text(side == "buy", _at(px_start, k), _at(px_end, k),
                                                  _at(px_hi, k), _at(px_lo, k), tick, push_min, px_dec)
                if _at_:
                    _mt = _at_
                    if _aw:
                        _mw = _aw
            rows.append((t0, t0 + el, head, state_label(st, side),
                         _line1(vr[k], buy_ratio, sell_ratio, k),
                         _line2(bid_ratio, ask_ratio, k),
                         st, bool(conf[k] >= float(weak_below)), True,
                         colour_of(st, side, _contra(k)), _mt, _ms, _mw, _raw(k, st, side, el)))
            continue
        if not ok[k]:
            rows.append((t0, t1, "%s - %s - %s" % (_clock(t0), _clock(t1), dur_text(t1 - t0)),
                         "-", "not enough history yet", ("", ""), ST_QUIET, False, False, C_QUIET, "", 0, "",
                         _raw(k, -1, "", t1 - t0)))
            continue
        st, side = _quadrant(heavy[k], big[k], up[k], sd[k])
        _mt, _mw, _ms = move_text(_at(px_start, k), _at(px_end, k), mv[k], flat[k], sr[k],
                                  slow_c, fast_c, px_dec)
        _strong = bool(conf[k] >= float(weak_below))
        if st == ST_ABSORB:
            # an absorbed row is measured off its HIGH (buy) or LOW (sell), not open-to-close
            _at_, _aw, _fr = absorb_move_text(side == "buy", _at(px_start, k), _at(px_end, k),
                                              _at(px_hi, k), _at(px_lo, k), tick, push_min, px_dec)
            if _at_:
                _mt = _at_
                if _aw:
                    _mw = _aw
                # ⚠ a third gate on the STRENGTH, and only for this state: however heavy the flow was, a
                # cycle that handed back less than the measured lower tercile of its push was not really
                # absorbed. NaN (no push worth measuring) does NOT downgrade -- absence is not evidence.
                if np.isfinite(_fr) and _fr < float(reject_weak):
                    _strong = False
        rows.append((t0, t1, "%s - %s - %s" % (_clock(t0), _clock(t1), dur_text(t1 - t0)),
                     state_label(st, side),
                     _line1(vr[k], buy_ratio, sell_ratio, k),
                     _line2(bid_ratio, ask_ratio, k),
                     st, _strong, False,
                     colour_of(st, side, _contra(k)), _mt, _ms, _mw, _raw(k, st, side, t1 - t0)))
    return rows


class _LookbackEdit(QtWidgets.QLineEdit):
    """The inline number editor. Escape is handled HERE rather than through an event filter on the panel:
    instrumented inside the running terminal, that filter only ever received ShortcutOverride, never the
    KeyPress, so Escape silently did nothing."""

    escaped = QtCore.Signal()

    def keyPressEvent(self, ev):
        if ev.key() == QtCore.Qt.Key_Escape:
            ev.accept()
            self.escaped.emit()
            return
        super().keyPressEvent(ev)


class FlowInterpPanel(QtWidgets.QAbstractScrollArea):
    """A vertical feed of cycle interpretations, newest at the top.

    Only the rows actually on screen are painted. At 400 cycles that is ~14 rows of four short strings, so a
    repaint is a handful of drawText calls whatever the history depth -- the list length never enters the
    per-frame cost."""

    cycleClicked = QtCore.Signal(float, float)      # (t_start, t_end) of the clicked row
    lookbackChanged = QtCore.Signal(int)           # the cycle lookback N, bottom-right

    PAD = 10
    GAP = 16          # between a label and the value that belongs beside it
    FOOT_H = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._ys: list = []             # each card's top offset (see _layout); the pane resizes before any rows land
        self._total = 0
        self._dark = True
        self._top_t = None
        self._hover = -1
        self._sel = -1                  # the row a PRICE-pane candle click pointed at, or -1
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.viewport().setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.viewport().setMouseTracking(True)
        self.setMinimumWidth(300)      # under this the price-move line starts to clip
        self.setMaximumWidth(620)
        self._hint_w = 330
        self._f_head = QtGui.QFont(); self._f_head.setPointSize(8)
        self._f_name = QtGui.QFont(); self._f_name.setPointSize(10); self._f_name.setBold(True)
        self._f_det = QtGui.QFont(); self._f_det.setPointSize(8)
        # the move reads as part of the interpretation, so it is bolder than the evidence lines below it
        self._f_move = QtGui.QFont(); self._f_move.setPointSize(8); self._f_move.setBold(True)
        self._f_title = QtGui.QFont(); self._f_title.setPointSize(8); self._f_title.setBold(True)
        self._f_lb = QtGui.QFont(); self._f_lb.setPointSize(8); self._f_lb.setBold(True)
        self._f_lbv = QtGui.QFont(); self._f_lbv.setPointSize(11); self._f_lbv.setBold(True)
        self._lb = 5                    # the cycle lookback, drawn in the footer band
        self._lb_hover = 0              # -1 over the left chevron, +1 over the right, +2 over the number
        self._lb_edit = None            # the inline editor, built the first time the number is clicked
        self.title = "INTERPRETATION"      # the terminal replaces this with config.PANE_TITLE_INTERP, the
        #                                    same string its hamburger toggle carries

    def sizeHint(self):
        return QtCore.QSize(int(self._hint_w), 600)

    # ---- the lookback control, drawn in the footer band ---------------------------------------------------
    LB_MIN, LB_MAX = 2, 20

    def lookback(self) -> int:
        return int(self._lb)

    def setLookback(self, n: int) -> None:
        """Set it without emitting -- the terminal calls this to restore a saved value."""
        v = max(self.LB_MIN, min(self.LB_MAX, int(n)))
        if v != self._lb:
            self._lb = v
            self.viewport().update()

    def _bump(self, d: int) -> None:
        v = max(self.LB_MIN, min(self.LB_MAX, self._lb + int(d)))
        if v != self._lb:
            self._lb = v
            self.viewport().update()
            self.lookbackChanged.emit(v)

    def _foot_rects(self):
        """(band, left chevron, value, right chevron) in viewport coordinates."""
        w = self.viewport().width(); h = self.viewport().height()
        band = QtCore.QRect(0, h - self.FOOT_H, w, self.FOOT_H)
        r = 22                                          # chevron hit box, comfortably clickable
        vw = 46                                         # the number: wide enough for three digits
        vx = w - self.PAD - r - vw
        return (band,
                QtCore.QRect(vx - r, band.y() + 2, r, self.FOOT_H - 4),
                QtCore.QRect(vx, band.y() + 2, vw, self.FOOT_H - 4),
                QtCore.QRect(vx + vw, band.y() + 2, r, self.FOOT_H - 4))

    # ---- typing the number ------------------------------------------------------------------------------
    def _begin_edit(self) -> None:
        """Click the number and type one. The chevrons nudge; this is for jumping straight to a value."""
        _b, _l, vrect, _r = self._foot_rects()
        if self._lb_edit is None:
            e = _LookbackEdit(self.viewport())
            e.setFont(self._f_lbv)
            e.setAlignment(QtCore.Qt.AlignCenter)
            e.setFrame(False)
            e.setValidator(QtGui.QIntValidator(self.LB_MIN, self.LB_MAX, e))
            e.returnPressed.connect(self._commit_edit)
            e.editingFinished.connect(self._commit_edit)
            e.escaped.connect(self._abandon_edit)   # Escape abandons rather than commits
            self._lb_edit = e
        e = self._lb_edit
        e.setStyleSheet(
            "QLineEdit { color: %s; background: %s; border: 1px solid %s; selection-background-color: %s; }"
            % ("#e6ebf0" if self._dark else "#1a1a1a", "#1b2026" if self._dark else "#eef1f4",
               "#3a434c" if self._dark else "#c9d0d6", "#3a6ea5"))
        e.setGeometry(vrect)
        e.setText(str(self._lb))
        e.show(); e.raise_(); e.setFocus(QtCore.Qt.MouseFocusReason); e.selectAll()

    def _commit_edit(self) -> None:
        e = self._lb_edit
        if e is None or not e.isVisible():
            return
        txt = e.text().strip()
        e.hide()
        self.viewport().update()
        if not txt:
            return
        try:
            v = int(txt)
        except ValueError:
            return
        v = max(self.LB_MIN, min(self.LB_MAX, v))
        if v != self._lb:
            self._lb = v
            self.viewport().update()
            self.lookbackChanged.emit(v)

    def _abandon_edit(self) -> None:
        """Escape: close the editor and leave the value exactly as it was."""
        if self._lb_edit is not None:
            self._lb_edit.hide()
            self.viewport().update()

    def _draw_footer(self, p, w, h):
        band, lrect, vrect, rrect = self._foot_rects()
        p.fillRect(band, QtGui.QColor("#141414" if self._dark else "#ffffff"))
        p.setPen(QtGui.QColor("#2a3138" if self._dark else "#e2e2e2"))
        p.drawLine(self.PAD, band.y(), w - self.PAD, band.y())
        p.setFont(self._f_lb)
        p.setPen(QtGui.QColor("#6f7a82" if self._dark else "#9a9a9a"))
        p.drawText(self.PAD, band.y() + self.FOOT_H - 8, "LOOKBACK")
        for rect, d, ch in ((lrect, -1, "\u2039"), (rrect, +1, "\u203a")):
            on = (self._lb_hover == d)
            live = (self._lb > self.LB_MIN) if d < 0 else (self._lb < self.LB_MAX)
            if on and live:
                p.fillRect(rect, QtGui.QColor(255, 255, 255, 20) if self._dark
                           else QtGui.QColor(0, 0, 0, 16))
            col = ("#d7dde3" if self._dark else "#333333") if live else ("#3d444b" if self._dark else "#cccccc")
            p.setFont(self._f_lbv); p.setPen(QtGui.QColor(col))
            p.drawText(rect, QtCore.Qt.AlignCenter, ch)
        if self._lb_edit is None or not self._lb_edit.isVisible():
            if self._lb_hover == 2:
                # a hairline under the number: the only hint it can be typed into
                p.fillRect(vrect, QtGui.QColor(255, 255, 255, 16) if self._dark
                           else QtGui.QColor(0, 0, 0, 12))
                p.setPen(QtGui.QColor("#6f7a82" if self._dark else "#9a9a9a"))
                p.drawLine(vrect.x() + 6, vrect.bottom() - 2, vrect.right() - 6, vrect.bottom() - 2)
            p.setFont(self._f_lbv)
            p.setPen(QtGui.QColor("#e6ebf0" if self._dark else "#1a1a1a"))
            p.drawText(vrect, QtCore.Qt.AlignCenter, str(self._lb))

    LB_TIP = ("<b>Cycle lookback</b><br>How many previous cycles every rating is measured against."
              "<br><br>One knob for the whole family: <b>CYCLE VOLUME</b>, <b>CYCLE BOOK</b>, "
              "<b>CYCLE SPEED</b> and the states in this feed. Volume and Speed use the last N of the "
              "<i>same side</i>, so they reach about twice as far back in time."
              "<br><br>Click the number to type one, or use the chevrons."
              "<br><br>Smaller reacts faster and is noisier; larger is steadier. Measured over 20 h, the mix "
              "shifts with it: BREAKOUT is 27% of cycles at 5 and 40% at 100, because a longer baseline is "
              "smoother and 'heavier than usual' and 'faster than usual' then coincide more often. The band "
              "cuts for the other panes were measured at 5.")

    def event(self, ev):
        """A tooltip over the footer only -- the rows below it have their own meaning and want no tooltip."""
        if ev.type() == QtCore.QEvent.ToolTip:
            pos = ev.pos()
            if pos.y() >= self.viewport().height() - self.FOOT_H:
                QtWidgets.QToolTip.showText(ev.globalPos(), self.LB_TIP, self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().event(ev)

    def _foot_hit(self, pos) -> int:
        _b, l, v, r = self._foot_rects()
        if l.contains(pos):
            return -1
        if r.contains(pos):
            return 1
        if v.contains(pos):
            return 2                                 # the number itself: click to type
        return 0

    # ---- layout: one CARD per cycle, and an hour divider where the clock's hour changes (2026-09-23) ----------
    # The feed was redesigned from four lines of text per cycle into a card that SHOWS its numbers (user
    # 2026-09-23: "completely redesign it ... beautifully designed so that it facilitates reading"): the state as
    # a chip, the price move as the headline, the effort x result QUADRANT the state is read from, the tape as
    # two bars around each side's own normal, the book as arrows. Cards share one height, so every index <->
    # pixel mapping stays a lookup in `_ys`; only the hour dividers make the offsets uneven.
    CARD_H = 112
    CARD_GAP = 6
    SEP_H = 22
    TOP = 22            # the pane's title band
    ROW_H = CARD_H + CARD_GAP      # (kept for anything that still reads the old name)

    def _hour(self, i):
        try:
            return str(self._rows[i][2])[:2]           # every head starts with the cycle's own clock
        except Exception:
            return ""

    def _layout(self):
        ys, y = [], 0
        for i in range(len(self._rows)):
            if i > 0 and self._hour(i) != self._hour(i - 1):
                y += self.SEP_H
            ys.append(y)
            y += self.CARD_H + self.CARD_GAP
        self._ys = ys
        self._total = y

    # ---- data -------------------------------------------------------------------------------------------
    def setRows(self, rows) -> None:
        """Replace the feed. Keeps the reader's place: if they have scrolled down into history, the scrollbar
        moves by however far the cards they were reading were pushed down by the new ones on top."""
        sb = self.verticalScrollBar()
        old_top, val = self._top_t, sb.value()
        _sel_t = (float(self._rows[self._sel][0])
                  if (self._sel != -1 and 0 <= self._sel < len(self._rows)) else None)
        self._rows = rows or []
        self._layout()
        if val > 0 and old_top is not None:
            added = 0
            for r in self._rows:
                if r[0] <= old_top + 1e-6:
                    break
                added += 1
            if 0 < added < len(self._ys):
                val += self._ys[added]
        # the selection follows its CYCLE, not its index: rows are prepended as cycles form, so holding the
        # index would slide the highlight onto a different cycle every time the feed grew
        if self._sel != -1 and _sel_t is not None:
            self._sel = next((i for i, r in enumerate(self._rows)
                              if abs(float(r[0]) - _sel_t) < 1e-6), -1)
        self._top_t = self._rows[0][0] if self._rows else None
        self._update_scroll()
        sb.setValue(min(val, sb.maximum()))
        self.viewport().update()

    def scrollToCycle(self, t0: float, centre: bool = True) -> int:
        """Bring the card for cycle `t0` into view and mark it. Returns its index, or -1 if it is not in the feed.

        The reverse of cycleClicked: clicking a candle in the PRICE pane asks the feed to show that cycle's
        reading. NEAREST start wins -- the candle's own x is the cycle's midpoint and the caller rounds."""
        if not self._rows:
            return -1
        best, bd = -1, None
        for i, r in enumerate(self._rows):
            d = abs(float(r[0]) - float(t0))
            if bd is None or d < bd:
                best, bd = i, d
        if best < 0:
            return -1
        self._sel = best
        if len(getattr(self, "_ys", ())) != len(self._rows):
            self._layout()
        sb = self.verticalScrollBar()
        top = self.PAD + self.TOP + self._ys[best]
        if centre:
            want = int(top - max(0, (self.viewport().height() - self.FOOT_H - self.CARD_H) // 2))
        else:
            want = top
        sb.setValue(max(0, min(want, sb.maximum())))
        self.viewport().update()
        return best

    def clearSelection(self) -> None:
        if self._sel != -1:
            self._sel = -1
            self.viewport().update()

    def setDark(self, dark: bool) -> None:
        if bool(dark) != self._dark:
            self._dark = bool(dark)
            self.viewport().update()

    def _update_scroll(self) -> None:
        sb = self.verticalScrollBar()
        if len(getattr(self, "_ys", ())) != len(self._rows):
            self._layout()
        # + FOOT_H so the last card can scroll clear of the lookback control rather than sitting under it
        total = self._total + self.PAD * 2 + self.TOP + self.FOOT_H
        sb.setRange(0, max(0, total - self.viewport().height()))
        sb.setPageStep(self.viewport().height())
        sb.setSingleStep(self.CARD_H // 2)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_scroll()
        if self._lb_edit is not None and self._lb_edit.isVisible():
            self._lb_edit.setGeometry(self._foot_rects()[2])

    # ---- interaction ------------------------------------------------------------------------------------
    def _row_at(self, y: int) -> int:
        ys = getattr(self, "_ys", [])
        if not ys:
            return -1
        yy = y + self.verticalScrollBar().value() - self.PAD - self.TOP
        i = bisect.bisect_right(ys, yy) - 1
        return int(i) if (0 <= i < len(self._rows) and yy < ys[i] + self.CARD_H) else -1

    def mouseMoveEvent(self, ev):
        pos = ev.position().toPoint()
        hv = self._foot_hit(pos)
        i = -1 if (hv or pos.y() >= self.viewport().height() - self.FOOT_H) else self._row_at(pos.y())
        if i != self._hover or hv != self._lb_hover:
            self._hover = i; self._lb_hover = hv
            self.viewport().update()

    def leaveEvent(self, ev):
        if self._hover != -1 or self._lb_hover:
            self._hover = -1; self._lb_hover = 0
            self.viewport().update()

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint()
        hv = self._foot_hit(pos)
        if hv == 2:
            self._begin_edit()
            return
        if hv:
            self._bump(hv)
            return
        if pos.y() >= self.viewport().height() - self.FOOT_H:
            return                                  # the footer band belongs to the control, not to a card
        i = self._row_at(pos.y())
        if i >= 0:
            r = self._rows[i]
            self.cycleClicked.emit(float(r[0]), float(r[1]))

    # ---- paint ------------------------------------------------------------------------------------------
    def paintEvent(self, ev):
        _t0 = time.perf_counter()
        try:
            self._paint_event(ev)
        finally:
            try:
                _cb = getattr(self.window(), "_perf_note_paint", None)
                if _cb is not None:
                    _cb(self, (time.perf_counter() - _t0) * 1000.0)
            except Exception:
                pass

    def _fonts(self):
        f = getattr(self, "_cf", None)
        if f is not None:
            return f
        # Consolas where Windows has it: the system fixed font there is Courier New, whose serifs read as dated
        mono = (QtGui.QFont("Consolas") if "Consolas" in QtGui.QFontDatabase.families()
                else QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        def mk(base, pt, bold=False):
            q = QtGui.QFont(base); q.setPointSizeF(pt); q.setBold(bold); return q
        sans = QtGui.QFont()
        self._cf = f = {"chip": mk(sans, 8, True), "meta": mk(mono, 8), "big": mk(sans, 15, True),
                        "small": mk(mono, 8), "label": mk(sans, 8), "tiny": mk(sans, 7), "sep": mk(mono, 7)}
        return f

    def _chip_text(self, st, name, raw):
        side = str((raw or {}).get("side") or (name.split()[-1] if name and name.split()[-1] in ("buy", "sell") else ""))
        if name == "-":
            return "Warming up"
        if st == ST_BREAK:
            return "Breakout · %s" % side
        if st == ST_ABSORB:
            return "Buyer absorbed" if side == "buy" else "Seller absorbed"
        if st == ST_VACUUM:
            return "Vacuum · %s" % side
        if st == ST_QUIET:
            return "Quiet"
        return "Forming"

    def _paint_event(self, ev):
        p = QtGui.QPainter(self.viewport())
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w = self.viewport().width(); h = self.viewport().height()
        dk = self._dark
        p.fillRect(0, 0, w, h, QtGui.QColor("#141414" if dk else "#ffffff"))
        F = self._fonts()
        dim = QtGui.QColor("#6f7a82" if dk else "#7a7a7a")
        det = QtGui.QColor("#9aa8b0" if dk else "#303030")

        p.setFont(self._f_title)
        p.setPen(QtGui.QColor("#7d8492" if dk else "#303030"))
        p.drawText(self.PAD, 15, self.title)
        p.setPen(QtGui.QColor("#2a3138" if dk else "#dddddd"))
        p.drawLine(self.PAD, 20, w - self.PAD, 20)

        if len(getattr(self, "_ys", ())) != len(self._rows):
            self._layout()
        p.save()
        p.setClipRect(0, 21, w, max(0, h - 21 - self.FOOT_H))
        off = self.verticalScrollBar().value()
        base = self.PAD + self.TOP - off
        ys = self._ys
        first = max(0, bisect.bisect_right(ys, off - self.PAD - self.TOP - self.CARD_H) - 1)
        x = self.PAD; cw = w - 2 * self.PAD
        for i in range(int(first), len(self._rows)):
            y = base + ys[i]
            if y > h:
                break
            if i > 0 and self._hour(i) != self._hour(i - 1):
                self._draw_sep(p, F, x, y - self.SEP_H, cw, i, dim)
            if y + self.CARD_H < 20:
                continue
            self._draw_card(p, F, i, x, y, cw, dim, det)
        p.restore()
        if not self._rows:
            p.setFont(self._f_det); p.setPen(dim)
            p.drawText(self.PAD, 44, "waiting for the first cycles")
        self._draw_footer(p, w, h)
        p.end()

    def _draw_sep(self, p, F, x, y, cw, i, dim):
        """The hour divider: this card's hour, then a hairline."""
        lab = "%s:00" % self._hour(i)
        p.setFont(F["sep"]); p.setPen(dim)
        fm = QtGui.QFontMetrics(F["sep"])
        p.drawText(x + 2, y + 15, lab)
        p.setPen(QtGui.QPen(QtGui.QColor("#2a3138" if self._dark else "#e3e3e3"), 1))
        p.drawLine(int(x + 8 + fm.horizontalAdvance(lab)), y + 11, int(x + cw), y + 11)

    def _draw_card(self, p, F, i, x, y, cw, dim, det):
        row = self._rows[i]
        (t0, t1, head, name, d1, d2, st, strong, forming, col, mv_txt, mv_sign, mv_word) = row[:13]
        raw = row[13] if len(row) > 13 and isinstance(row[13], dict) else {}
        dk = self._dark
        col = max(0, min(len(BAR_COL) - 1, int(col)))
        scol = QtGui.QColor(BAR_COL[col])
        tcol = QtGui.QColor((TXT_DARK if dk else TXT_LIGHT)[col])
        mvp = MOVE_DARK if dk else MOVE_LIGHT
        nan = float("nan")
        def g(k):
            try:
                v = float(raw.get(k, nan))
                return v if math.isfinite(v) else nan
            except Exception:
                return nan

        # ---- the card itself
        rect = QtCore.QRectF(x, y, cw, self.CARD_H)
        path = QtGui.QPainterPath(); path.addRoundedRect(rect, 8, 8)
        # light = Chart Style Simple BW (user 2026-09-23: "it should be white"): white cards on the white page,
        # held apart by a hairline instead of a tint
        fill = QtGui.QColor("#1a1f25" if dk else "#ffffff")
        if i == self._hover:
            fill = QtGui.QColor("#20262d" if dk else "#f5f6f7")
        p.fillPath(path, fill)
        if i == self._sel:
            p.setPen(QtGui.QPen(QtGui.QColor("#7FB2FF" if dk else "#0B4FA8"), 1.6))
        else:
            _bp = QtGui.QPen(QtGui.QColor("#262d34" if dk else "#d9dde1"), 1)
            if forming:
                _bp.setStyle(QtCore.Qt.DashLine)
            p.setPen(_bp)
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawPath(path)
        # the state rail, clipped to the rounded card: solid and 4 px for a confident reading, faded and
        # narrower for a weak one, dashed while the cycle is still forming
        p.save(); p.setClipPath(path)
        rc = QtGui.QColor(scol)
        if not strong:
            rc.setAlpha(110)
        rw = 4 if strong else 3
        if forming:
            _yy = y + 2
            while _yy < y + self.CARD_H - 2:
                p.fillRect(QtCore.QRectF(x, _yy, rw, 6), rc)
                _yy += 10
        else:
            p.fillRect(QtCore.QRectF(x, y, rw, self.CARD_H), rc)
        p.restore()

        L = x + 14
        qx = x + cw - 12 - 44
        R = qx - 10                                   # the left column's right edge

        # ---- line 1: the state chip, then the clock and the duration; live / weak at the right
        chip = self._chip_text(int(st), str(name), raw)
        p.setFont(F["chip"])
        fmc = QtGui.QFontMetrics(F["chip"])
        crect = QtCore.QRectF(L, y + 8, fmc.horizontalAdvance(chip) + 16, 18)
        filled = int(st) in (ST_BREAK, ST_ABSORB)
        if filled:
            cb = QtGui.QColor(scol); cb.setAlpha(58 if dk else 44)
            p.setPen(QtCore.Qt.NoPen); p.setBrush(cb)
        else:
            cb = QtGui.QColor(scol); cb.setAlpha(170)
            p.setPen(QtGui.QPen(cb, 1)); p.setBrush(QtCore.Qt.NoBrush)
        p.drawRoundedRect(crect, 9, 9)
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(tcol)
        p.drawText(crect, QtCore.Qt.AlignCenter, chip)
        parts = str(head).split(" - ")
        clock = parts[0] if parts else ""
        dur = parts[-1] if len(parts) > 1 else ""
        p.setFont(F["meta"]); p.setPen(dim)
        p.drawText(QtCore.QPointF(crect.right() + 8, y + 21), "%s · %s" % (clock, dur) if dur else clock)
        if forming:
            p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(mvp[0]))
            # the dot breathes with the second: the feed repaints every tick of a forming cycle anyway
            if int(time.time()) % 2 == 0:
                p.drawEllipse(QtCore.QPointF(x + cw - 40, y + 17), 3.2, 3.2)
            p.setBrush(QtCore.Qt.NoBrush)
            p.setFont(F["label"]); p.setPen(QtGui.QColor(mvp[0]))
            p.drawText(QtCore.QPointF(x + cw - 33, y + 21), "live")
        elif not strong and str(name) != "-":
            p.setFont(F["label"]); p.setPen(dim)
            p.drawText(QtCore.QPointF(x + cw - 12 - QtGui.QFontMetrics(F["label"]).horizontalAdvance("weak"), y + 21),
                       "weak")

        # ---- the headline: the move in ticks -- or, for an absorbed cycle, how much of its push was given back
        mv = g("mv"); p0 = g("px0"); p1 = g("px1")
        p.save(); p.setClipRect(QtCore.QRectF(x, y, R - x, self.CARD_H))
        gb = g("gb"); push = g("push")
        if int(st) == ST_ABSORB and math.isfinite(gb) and math.isfinite(push):
            big = "%d%%" % int(round(gb * 100))
            p.setFont(F["big"]); p.setPen(tcol)
            p.drawText(QtCore.QPointF(L, y + 50), big)
            bx = L + QtGui.QFontMetrics(F["big"]).horizontalAdvance(big) + 8
            ext = g("hi") if raw.get("side") == "buy" else g("lo")
            p.setFont(F["small"]); p.setPen(det)
            txt = "of a %dt push" % int(round(push))
            if math.isfinite(ext) and math.isfinite(p1):
                # the prices only when they fit: on a narrow feed the push is the part worth keeping
                _px = "  %s %.2f → %.2f" % ("hi" if raw.get("side") == "buy" else "lo", ext, p1)
                if bx + QtGui.QFontMetrics(F["small"]).horizontalAdvance(txt + _px) <= R:
                    txt += _px
            p.drawText(QtCore.QPointF(bx, y + 49), txt)
            # the give-back bar: the whole track is the push, the filled part (from its tip) what was handed back
            tr = QtCore.QRectF(L, y + 56, max(10.0, R - L), 5)
            tk = QtGui.QColor(scol); tk.setAlpha(60)
            p.setPen(QtCore.Qt.NoPen); p.setBrush(tk); p.drawRoundedRect(tr, 2.5, 2.5)
            fw = tr.width() * max(0.0, min(1.0, gb))
            p.setBrush(scol); p.drawRoundedRect(QtCore.QRectF(tr.right() - fw, tr.y(), fw, 5), 2.5, 2.5)
            p.setBrush(QtCore.Qt.NoBrush)
        elif str(name) == "-":
            p.setFont(F["big"]); p.setPen(dim)
            p.drawText(QtCore.QPointF(L, y + 50), "—")
            p.setFont(F["small"]); p.setPen(det)
            p.drawText(QtCore.QPointF(L + 28, y + 49), "not enough history yet")
        else:
            if math.isfinite(mv):
                n = int(round(mv))
                big = ("+%dt" % n) if n > 0 else (("−%dt" % -n) if n < 0 else "0t")
            else:
                big = "—"
            p.setFont(F["big"]); p.setPen(QtGui.QColor(mvp[max(0, min(2, int(mv_sign) + 1))]))
            p.drawText(QtCore.QPointF(L, y + 50), big)
            bx = L + QtGui.QFontMetrics(F["big"]).horizontalAdvance(big) + 8
            p.setFont(F["small"]); p.setPen(det)
            if math.isfinite(p0) and math.isfinite(p1):
                p.drawText(QtCore.QPointF(bx, y + 49), "%.2f → %.2f" % (p0, p1))
            elif mv_txt:
                p.drawText(QtCore.QPointF(bx, y + 49), str(mv_txt))
        p.restore()

        # ---- tape: each side's aggressive $/s against ITS OWN normal, as a bar either side of 1x
        def ratio_bar(bx, by, v, c):
            bw = 40.0
            tr = QtCore.QRectF(bx, by, bw, 6)
            p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor("#262d34" if dk else "#eceef0"))
            p.drawRoundedRect(tr, 3, 3)
            if math.isfinite(v) and v > 0:
                ext = max(-1.5, min(1.5, math.log2(v))) / 1.5 * (bw / 2)
                p.setBrush(QtGui.QColor(c))
                if ext >= 0:
                    p.drawRoundedRect(QtCore.QRectF(bx + bw / 2, by, ext, 6), 3, 3)
                else:
                    p.drawRoundedRect(QtCore.QRectF(bx + bw / 2 + ext, by, -ext, 6), 3, 3)
            p.setBrush(QtCore.Qt.NoBrush)
            p.setPen(QtGui.QPen(QtGui.QColor("#6f7a82" if dk else "#9aa3aa"), 1))
            p.drawLine(QtCore.QPointF(bx + bw / 2, by - 2), QtCore.QPointF(bx + bw / 2, by + 8))
            p.setFont(F["small"]); p.setPen(det)
            p.drawText(QtCore.QPointF(bx + bw + 4, by + 6.5), ("%.2f×" % v) if math.isfinite(v) else "–")
        p.setFont(F["label"]); p.setPen(dim)
        p.drawText(QtCore.QPointF(L, y + 78), "tape")
        ratio_bar(L + 34, y + 71, g("buy"), "#26A69A")
        ratio_bar(L + 34 + 40 + 44, y + 71, g("sell"), "#EF5350")

        # ---- book: the resting orders on each side, as a change against their own recent level
        def pct(px, py, label, r):
            p.setFont(F["label"]); p.setPen(det)
            p.drawText(QtCore.QPointF(px, py), label)
            px += QtGui.QFontMetrics(F["label"]).horizontalAdvance(label) + 4
            if not math.isfinite(r):
                p.setPen(dim); p.drawText(QtCore.QPointF(px, py), "–"); return px + 12
            n = int(round((r - 1.0) * 100.0))
            if n == 0:
                s = "0%"; c = dim
            else:
                s = ("▲%d%%" % n) if n > 0 else ("▼%d%%" % -n)
                c = QtGui.QColor(mvp[2] if n > 0 else mvp[0])
            p.setFont(F["small"]); p.setPen(c)
            p.drawText(QtCore.QPointF(px, py), s)
            return px + QtGui.QFontMetrics(F["small"]).horizontalAdvance(s) + 12
        p.setFont(F["label"]); p.setPen(dim)
        p.drawText(QtCore.QPointF(L, y + 98), "book")
        # the two columns sit exactly under the two tape bars: buyers left, sellers right, in both rows
        pct(L + 34, y + 98, "buyers", g("bid"))
        pct(L + 34 + 40 + 44, y + 98, "sellers", g("ask"))

        # ---- the quadrant: the two numbers the state is READ from -- flow (effort, left -> right) against speed
        # (result, bottom -> top). Breakout top-right, absorbed bottom-right, vacuum top-left, quiet bottom-left.
        qy = y + 30; q = 44
        for (cx, cy, c) in ((0, 0, BAR_COL[C_VACUUM]), (1, 0, BAR_COL[C_BREAK_BUY]),
                            (0, 1, BAR_COL[C_QUIET]), (1, 1, BAR_COL[C_ABSORB_BUY])):
            qc = QtGui.QColor(c); qc.setAlpha(46 if dk else 38)
            p.fillRect(QtCore.QRectF(qx + cx * q / 2, qy + cy * q / 2, q / 2, q / 2), qc)
        p.setPen(QtGui.QPen(QtGui.QColor("#4a545c" if dk else "#c3c9ce"), 1))
        p.drawLine(QtCore.QPointF(qx + q / 2, qy), QtCore.QPointF(qx + q / 2, qy + q))
        p.drawLine(QtCore.QPointF(qx, qy + q / 2), QtCore.QPointF(qx + q, qy + q / 2))
        vr = g("vr"); sr = g("sr")
        if math.isfinite(vr) and vr > 0 and math.isfinite(sr) and sr > 0:
            ex = max(-1.5, min(1.5, math.log2(vr))) / 1.5 * (q / 2 - 4)
            ey = max(-1.5, min(1.5, math.log2(sr))) / 1.5 * (q / 2 - 4)
            if raw.get("flat"):
                ey = min(ey, -2.0)                    # a flat move is "small" however fast its few ticks were
            dc = QtCore.QPointF(qx + q / 2 + ex, qy + q / 2 - ey)
            if strong:
                p.setPen(QtCore.Qt.NoPen); p.setBrush(scol)
            else:
                p.setPen(QtGui.QPen(scol, 1.6)); p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(dc, 4, 4)
            p.setBrush(QtCore.Qt.NoBrush)
        p.setFont(F["tiny"])
        fmt = QtGui.QFontMetrics(F["tiny"])
        ft = ("flow %.2f×" % vr) if math.isfinite(vr) else "flow –"
        p.setPen(dim)
        p.drawText(QtCore.QPointF(qx + q / 2 - fmt.horizontalAdvance(ft) / 2, qy + q + 13), ft)
        if mv_word and int(st) != ST_ABSORB:
            sw = str(mv_word)
            p.setPen(det)
            p.drawText(QtCore.QPointF(qx + q / 2 - fmt.horizontalAdvance(sw) / 2, qy + q + 25), sw)
