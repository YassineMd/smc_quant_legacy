# -*- coding: utf-8 -*-
"""HLH Volume Profile -- the DRAWING layer and the per-window overlay state.

Two canvases draw the same periods: the Mode-10 candle canvas (x = bar index) and the Flow mode PRICE pane
(x = epoch seconds). Everything time-shaped goes through an `xmap` (time -> x) so the geometry code is written
once; the engine (hlh_profile) never sees x at all.

Perf shape, in the spirit of every other overlay in this terminal:
  * a finished period is COMPUTED once (HlhOverlay.periods) and its picture is BUILT once per x-mapping
    (a new bar on the candle canvas re-keys the mapping; Flow mode's identity mapping never does);
  * the forming period recomputes only when the feed's rev moved (one REST poll every HLH_POLL_SECS);
  * one QPicture per period, replayed only when its x extent meets the exposed rect; labels are painted in
    DEVICE space from a second item (pixel-sized text, culled to the exposed rect), so a zoom or pan never
    re-lays anything out -- see BpLabelsItem for the pattern and the deviceTransform() trap it documents.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import config
from . import hlh_profile as H
from .hlh_feed import HlhFeed

# ---------------------------------------------------------------------------- the Pine palette
_D_COLS = ("#e91e63", "#00bcd4", "#9c27b0", "#4caf50", "#ff9800")   # D1, then D2..D5 repeating
C_HIST, C_LOW, C_HIGH, C_POC, C_SHARED, C_UNCOV = "#9598a1", "#f0b26b", "#6b9bd1", "#ffd54f", "#f23645", "#9c27b0"


def qcol(hexstr: str, tr: float = 0.0) -> QtGui.QColor:
    """A Pine colour with a Pine TRANSPARENCY (0 = opaque, 100 = invisible)."""
    c = QtGui.QColor(hexstr)
    c.setAlpha(max(0, min(255, int(round(255 * (100.0 - tr) / 100.0)))))
    return c


def d_colour(num: int) -> str:
    if num == 0:
        return C_UNCOV
    if num == 1:
        return _D_COLS[0]
    return _D_COLS[1 + (num - 2) % 4]


def _pen(col: QtGui.QColor, width: float) -> QtGui.QPen:
    pn = QtGui.QPen(col)
    pn.setWidthF(float(width))
    pn.setCosmetic(True)                       # pixel width whatever the view transform
    return pn


# ---------------------------------------------------------------------------- x mappings
class IdentityXMap:
    key = ("secs",)

    def __call__(self, t):
        t = np.asarray(t, dtype=np.float64)
        return float(t) if t.ndim == 0 else t


class BarXMap:
    """time -> x on the candle canvas, where bar i is drawn at x = i and spans [i - 0.5, i + 0.5]. Piecewise
    linear between bar starts (a busy half hour holds more buckets and is wider, honestly); past the last
    bar's start the last bar's length carries on for ONE more bar and then clamps, so the forming period's
    level lines reach the live edge without running off into a future the bucket chart does not have."""

    def __init__(self, starts, tf_secs: float = 60.0):
        st = np.asarray(starts, dtype=np.float64)
        n = int(st.shape[0])
        if n > 1 and np.any(np.diff(st) <= 0):                          # np.interp needs strictly increasing
            st = np.maximum.accumulate(st + np.arange(n) * 1e-6)
        if n > 1:
            d = np.diff(st[-21:])
            dur = float(np.median(d)) if d.size else float(tf_secs)
        else:
            dur = float(tf_secs)
        self.dur = max(dur, 1e-3)
        self.n = n
        self.kt = np.concatenate((st, [st[-1] + self.dur])) if n else np.array([0.0, 1.0])
        self.kx = np.arange(n + 1, dtype=np.float64) - 0.5
        self.key = ("bars", n, float(st[0]) if n else 0.0, float(st[-1]) if n else 0.0)

    def __call__(self, t):
        t = np.asarray(t, dtype=np.float64)
        x = np.interp(t, self.kt, self.kx)
        over = t > self.kt[-1]
        if np.any(over):
            x = np.where(over, np.minimum(self.n + 0.5, self.n - 0.5 + (t - self.kt[-1]) / self.dur), x)
        return float(x) if x.ndim == 0 else x


# ---------------------------------------------------------------------------- one period's geometry
class Label:
    __slots__ = ("x", "y", "text", "anchor", "bg", "fg", "small", "w", "h")

    def __init__(self, x, y, text, anchor, bg, fg, small):
        self.x = float(x); self.y = float(y); self.text = str(text); self.anchor = anchor
        self.bg = bg; self.fg = fg; self.small = bool(small); self.w = 0.0; self.h = 0.0


def build_period(res: H.PeriodResult, xmap, cand_secs: float, bloc_only: bool, dark: bool,
                 p: Optional[H.Params] = None) -> Tuple[list, List[Label]]:
    """The Pine's DRAW section for one period: ([(x_lo, x_hi, QPicture), ...], labels), pictures in plot
    coordinates. TWO pictures: the PROFILE (histogram, rows, POC, legs) spans only the period's first
    width_pct, the SPAN (levels, time profile, block lines) the whole period -- a live-edge sliver of the
    forming day then replays the span part alone (measured: the profile is ~40% of the primitives)."""
    cfg = config
    pic = QtGui.QPicture()
    pic2 = QtGui.QPicture()
    labels: List[Label] = []
    txt_dark = QtGui.QColor(0, 0, 0) if not dark else QtGui.QColor(255, 255, 255)
    txt_dim = QtGui.QColor(txt_dark); txt_dim.setAlpha(89)
    pfx = "W " if res.is_week else ""
    x_s = xmap(res.t_first)
    x_e = xmap(res.t_last + cand_secs)
    x_mid = xmap(res.per_mid)
    x_end = xmap(res.per_end)
    x_lo = float(min(x_s, x_mid)); x_hi = float(max(x_e, x_end))
    if res.degenerate or not res.maxVol > 0:
        return [], labels
    lo, step, vp, maxVol = res.lo, res.step, res.vp, res.maxVol
    rows = int(vp.shape[0])
    maxW = max(1e-9, (x_e - x_s) * float(cfg.HLH_WIDTH_PCT) / 100.0)
    min_w = maxW / 80.0                       # the Pine's 1-bar floor on a ~300-bar day, in x units

    def rowW(rv: float) -> float:
        return max(min_w, maxW * rv / maxVol) if rv > 0 else min_w

    pt = QtGui.QPainter(pic)
    pt.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
    ps = QtGui.QPainter(pic2)
    ps.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
    try:
        if not bloc_only:
            # -- the histogram
            pts = [QtCore.QPointF(x_s, lo)]
            for rr in range(rows):
                rv = float(vp[rr])
                w = max(min_w, maxW * rv / maxVol) if rv > 0 else 0.0
                pts.append(QtCore.QPointF(x_s + w, lo + rr * step))
                pts.append(QtCore.QPointF(x_s + w, lo + (rr + 1) * step))
            pts.append(QtCore.QPointF(x_s, lo + rows * step))
            pt.setPen(_pen(qcol(C_HIST, 40), 1.0))
            pt.setBrush(qcol(C_HIST, 55))
            pt.drawPolygon(QtGui.QPolygonF(pts))
            pt.setPen(QtCore.Qt.PenStyle.NoPen)
            # -- LOW rows (orange; faded when used up; red when shared and wide; hidden in an uncovered area)
            for i, a in enumerate(res.lowS):
                if cfg.HLH_UNCOVERED and not res.covered[a]:
                    continue
                if res.low_red(i, float(cfg.HLH_SHARED_PCT)):
                    bg = qcol(C_SHARED, 25)
                elif res.used[a]:
                    bg = qcol(C_LOW, cfg.HLH_DIM_TR)
                else:
                    bg = qcol(C_LOW, 25)
                y0 = lo + a * step; y1 = lo + (res.lowE[i] + 1) * step
                pt.fillRect(QtCore.QRectF(x_s, y0, rowW(float(vp[a])), y1 - y0), bg)
            # -- HIGH rows (blue; faded when used up unless it is an apex)
            for i, a in enumerate(res.highS):
                bg = qcol(C_HIGH, cfg.HLH_DIM_TR) if (res.used[a] and not res.isApex[a]) else qcol(C_HIGH, 25)
                y0 = lo + a * step; y1 = lo + (res.highE[i] + 1) * step
                pt.fillRect(QtCore.QRectF(x_s, y0, rowW(float(vp[a])), y1 - y0), bg)
            # -- the purple HIGH of each uncovered area
            for i, a in enumerate(res.uncS):
                alive = res.pds[i].alive if i < len(res.pds) else True
                bg = qcol(C_UNCOV, 25) if alive else qcol(C_UNCOV, cfg.HLH_DIM_TR)
                y0 = lo + a * step; y1 = lo + (res.uncE[i] + 1) * step
                pt.fillRect(QtCore.QRectF(x_s, y0, rowW(float(vp[a])), y1 - y0), bg)
            # -- POC
            y0 = lo + res.pS * step; y1 = lo + (res.pE + 1) * step
            pt.fillRect(QtCore.QRectF(x_s, y0, maxW, y1 - y0), qcol(C_POC, 15))

        # -- the Ds: legs, name, levels
        x_lvl0 = xmap(res.t_first)
        for d in res.allDs:
            if not d.alive:
                continue
            col = QtGui.QColor(d_colour(d.num))
            axX = x_s + rowW(d.apexV)
            axY = lo + (d.a + d.b + 1) / 2.0 * step
            if not bloc_only:
                for seq, fb in ((d.up, d.fbUp), (d.dn, d.fbDn)):
                    if seq:
                        for j, lid in enumerate(seq):
                            a = res.lowS[lid]
                            lx = x_s + rowW(float(vp[a]))
                            ly = lo + (a + res.lowE[lid] + 1) / 2.0 * step
                            if cfg.HLH_NUM_LOWS:
                                labels.append(Label(lx, ly, str(j + 1), "left", None, col, False))
                            if j == len(seq) - 1:
                                pt.setPen(_pen(col, cfg.HLH_D_WIDTH))
                                pt.drawLine(QtCore.QPointF(axX, axY), QtCore.QPointF(lx, ly))
                    elif fb >= 0:
                        fx = x_s + rowW(float(vp[fb]))
                        pt.setPen(_pen(col, cfg.HLH_D_WIDTH))
                        pt.drawLine(QtCore.QPointF(axX, axY), QtCore.QPointF(fx, lo + (fb + 0.5) * step))
                labels.append(Label(axX, axY, pfx + d.name, "left", qcol(d_colour(d.num), 85), col, False))
                topRow, botRow = res.leg_end_rows(d)
                if cfg.HLH_SHOW_LVL:
                    ps.setPen(_pen(col, cfg.HLH_LVL_WIDTH))
                    if topRow >= 0:
                        yUp = lo + (topRow + 1) * step
                        ps.drawLine(QtCore.QPointF(x_lvl0, yUp), QtCore.QPointF(x_end, yUp))
                    if botRow >= 0:
                        yDn = lo + botRow * step
                        ps.drawLine(QtCore.QPointF(x_lvl0, yDn), QtCore.QPointF(x_end, yDn))

        # -- time profile of every D area, the blocs, the Block Lines, the labels
        if cfg.HLH_SHOW_TP and res.areas:
            t0 = res.per_mid; binS = res.bin_secs; nBin = res.n_bins
            xb = xmap(t0 + binS * np.arange(nBin + 1, dtype=np.float64))     # bin edges, once
            for ai, ar in enumerate(res.areas):
                mx = float(ar.m.max()) if ar.m.size else 0.0
                if not mx > 0:
                    continue
                col = QtGui.QColor(d_colour(ar.num))
                kept = np.zeros(nBin, dtype=bool)
                my_blocs = [bb for bb in res.blocs if bb.area == ai]
                for bb in my_blocs:
                    if bb.keep:
                        kept[bb.bs:bb.be] = True
                if not bloc_only:
                    # ⚠ ONE filled polygon per area, not per-bin fillRect: a TRANSLUCENT fillRect has no fast
                    # path in Qt's raster engine (144 alpha rects 6.2 ms vs 3 polygons 0.8 ms for the same
                    # pixels, measured). What remains is the alpha compositing of the band area itself.
                    pts = [QtCore.QPointF(float(xb[0]), ar.yBot)]
                    span_y = ar.yTop - ar.yBot
                    for b in range(nBin):
                        yy = ar.yBot if kept[b] else ar.yBot + span_y * float(ar.m[b]) / mx
                        pts.append(QtCore.QPointF(float(xb[b]), yy)); pts.append(QtCore.QPointF(float(xb[b + 1]), yy))
                    pts.append(QtCore.QPointF(float(xb[nBin]), ar.yBot))
                    ps.setPen(_pen(qcol(d_colour(ar.num), max(40, cfg.HLH_TP_DIM_TR)), 1.0))
                    ps.setBrush(qcol(d_colour(ar.num), cfg.HLH_TP_DIM_TR))
                    ps.drawPolygon(QtGui.QPolygonF(pts))
                totM = totV = 0.0
                for bb in my_blocs:
                    totM += bb.mins; totV += bb.vol
                    if bb.keep and not bloc_only:
                        kp = [QtCore.QPointF(float(xb[bb.bs]), ar.yBot)]
                        for b in range(bb.bs, bb.be):
                            yy = ar.yBot + (ar.yTop - ar.yBot) * float(ar.m[b]) / mx
                            kp.append(QtCore.QPointF(float(xb[b]), yy)); kp.append(QtCore.QPointF(float(xb[b + 1]), yy))
                        kp.append(QtCore.QPointF(float(xb[bb.be]), ar.yBot))
                        ps.setPen(_pen(qcol(d_colour(ar.num), 40), 1.0))
                        ps.setBrush(qcol(d_colour(ar.num), cfg.HLH_TP_TR))
                        ps.drawPolygon(QtGui.QPolygonF(kp))
                    if cfg.HLH_SHOW_VA and ar.r1 >= ar.r0 and bb.vaLo >= 0:
                        lc = col if bb.keep else qcol(d_colour(ar.num), 60)
                        yVh = lo + (bb.vaHi + 1) * step; yVl = lo + bb.vaLo * step
                        ps.setPen(_pen(lc, 2 if bloc_only else cfg.HLH_VA_WIDTH))
                        ps.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                        xa, xbb = float(xb[bb.bs]), float(xb[bb.be])
                        ps.drawLine(QtCore.QPointF(xa, yVh), QtCore.QPointF(xbb, yVh))
                        ps.drawLine(QtCore.QPointF(xa, yVl), QtCore.QPointF(xbb, yVl))
                    if cfg.HLH_TP_BLOCS:
                        txt = ((bb.tag + "\n") if bb.tag else "") + "B%d\n%s\n%s" % (bb.num, H.fmt_dur(bb.mins), H.fmt_vol(bb.vol))
                        labels.append(Label(float(xmap(t0 + (bb.bs + bb.be) * binS / 2.0)), ar.yBot, txt, "up",
                                            qcol(d_colour(ar.num), 80 if bb.keep else 95),
                                            txt_dark if bb.keep else txt_dim, True))
                if not bloc_only:
                    if cfg.HLH_TP_PEAK:
                        pk = int(np.argmax(ar.m))
                        z = H._zone(cfg.HLH_TZ)
                        fmt = "%a %H:%M" if res.is_week else "%H:%M"
                        ptxt = "%s-%s  %d min" % (datetime.fromtimestamp(t0 + pk * binS, z).strftime(fmt),
                                                  datetime.fromtimestamp(t0 + (pk + 1) * binS, z).strftime(fmt), int(round(mx)))
                        labels.append(Label(float(xmap(t0 + pk * binS + binS / 2.0)), ar.yTop, ptxt, "down",
                                            qcol(d_colour(ar.num), 85), col, False))
                    if cfg.HLH_TP_TOTAL:
                        nb = len(my_blocs)
                        labels.append(Label(float(xb[0]), ar.yTop,
                                            "%s%s area: %s | vol %s | %d %s" % (pfx, ar.name, H.fmt_dur(totM), H.fmt_vol(totV),
                                                                             nb, "bloc" if nb == 1 else "blocs"),
                                            "lower_left", qcol(d_colour(ar.num), 70), txt_dark, True))
    finally:
        pt.end()
        ps.end()
    out = []
    if not bloc_only:
        out.append((float(x_s), float(x_s + maxW), pic))
    out.append((x_lo, x_hi, pic2))
    return out, labels


# ---------------------------------------------------------------------------- the graphics items
class HlhPicsItem(pg.GraphicsObject):
    """The pictures of every period, replayed only where the exposed rect meets a period's x extent."""

    def __init__(self):
        super().__init__()
        self._pics: List[Tuple[float, float, QtGui.QPicture]] = []
        self._replayed = 0
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    def set_pics(self, pics) -> None:
        self._pics = list(pics)
        self.update()

    def boundingRect(self):
        vb = self.getViewBox()
        try:
            return QtCore.QRectF(vb.viewRect()) if vb is not None else QtCore.QRectF()
        except Exception:
            return QtCore.QRectF()

    def paint(self, p, *args):
        if not self._pics:
            self._replayed = 0
            return
        _l, _r = -np.inf, np.inf
        try:
            _opt = args[0] if args else None
            _er = _opt.exposedRect if _opt is not None else None
            if _er is not None and _er.isValid():
                _l, _r = float(_er.left()), float(_er.right())
        except Exception:
            pass
        n = 0
        for xlo, xhi, pic in self._pics:
            if xhi >= _l and xlo <= _r:
                p.drawPicture(0, 0, pic)
                n += 1
        self._replayed = n


class HlhLabelsItem(pg.GraphicsObject):
    """Every label of every period in ONE item, painted in DEVICE space (pixel-sized text) and culled to the
    exposed rect. Sizes are measured once, when the labels are set."""

    def __init__(self):
        super().__init__()
        self._lbl: List[Label] = []
        self._note: Optional[str] = None
        self._f_tiny = QtGui.QFont("Consolas", 8)
        self._f_small = QtGui.QFont("Consolas", 9)
        self._f_small.setBold(True)
        self._fm_tiny = QtGui.QFontMetricsF(self._f_tiny)
        self._fm_small = QtGui.QFontMetricsF(self._f_small)
        self._painted = 0
        self._img_cache: Dict[tuple, QtGui.QImage] = {}   # (text, bg, fg, small, dpr) -> the rendered label
        self._rendered = 0                                # images rendered by the LAST paint (telemetry)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    def _label_image(self, lb: Label, dpr: float) -> QtGui.QImage:
        """One label rendered ONCE (rounded background + multi-line text) at the device pixel ratio, then
        blitted on every paint: a drawText with layout is ~80 us, a small drawImage ~5 us."""
        key = (lb.text, lb.bg.rgba() if lb.bg is not None else 0, lb.fg.rgba(), lb.small, dpr)
        im = self._img_cache.get(key)
        if im is not None:
            return im
        if len(self._img_cache) > 4000:
            self._img_cache.clear()
        wpx = max(1, int(math.ceil(lb.w * dpr))); hpx = max(1, int(math.ceil(lb.h * dpr)))
        im = QtGui.QImage(wpx, hpx, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        im.setDevicePixelRatio(dpr)
        im.fill(0)
        q = QtGui.QPainter(im)
        try:
            q.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            q.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
            q.setFont(self._f_small if lb.small else self._f_tiny)
            rect = QtCore.QRectF(0.0, 0.0, lb.w, lb.h)
            if lb.bg is not None and lb.bg.alpha() > 0:
                q.setPen(QtCore.Qt.PenStyle.NoPen)
                q.setBrush(lb.bg)
                q.drawRoundedRect(rect, 3.0, 3.0)
            q.setPen(lb.fg)
            q.drawText(rect.adjusted(3.0, 2.0, -3.0, -2.0),
                       int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop), lb.text)
        finally:
            q.end()
        self._img_cache[key] = im
        self._rendered += 1
        return im

    def set_labels(self, labels, note: Optional[str] = None) -> None:
        flags = int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        for lb in labels:
            if lb.w <= 0.0:
                fm = self._fm_small if lb.small else self._fm_tiny
                r = fm.boundingRect(QtCore.QRectF(0, 0, 600, 200), flags, lb.text)
                lb.w = float(r.width()) + 6.0; lb.h = float(r.height()) + 4.0
        self._lbl = list(labels)
        self._note = note
        self.update()

    def boundingRect(self):
        vb = self.getViewBox()
        try:
            return QtCore.QRectF(vb.viewRect()) if vb is not None else QtCore.QRectF()
        except Exception:
            return QtCore.QRectF()

    def paint(self, p, *args):
        if not self._lbl and not self._note:
            self._painted = 0
            return
        tr = QtGui.QTransform(p.transform())           # ⚠ never deviceTransform() in paint() (segfault)
        vp = p.viewport()
        wdev = float(vp.width()); hdev = float(vp.height())
        _ex = None
        try:
            _opt = args[0] if args else None
            _er = _opt.exposedRect if _opt is not None else None
            if _er is not None and _er.isValid():
                _ex = tr.mapRect(_er)
        except Exception:
            _ex = None
        p.save()
        p.resetTransform()
        try:
            dpr = float(p.device().devicePixelRatio())
        except Exception:
            dpr = 1.0
        flags = int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        n = 0
        self._rendered = 0
        for lb in self._lbl:
            pt = tr.map(QtCore.QPointF(lb.x, lb.y))
            px_, py_ = pt.x(), pt.y()
            w, h = lb.w, lb.h
            if lb.anchor == "left":
                rx, ry = px_ + 4.0, py_ - h / 2.0
            elif lb.anchor == "up":                        # the label hangs BELOW its point
                rx, ry = px_ - w / 2.0, py_ + 3.0
            elif lb.anchor == "down":                      # ... or sits ABOVE it
                rx, ry = px_ - w / 2.0, py_ - h - 3.0
            else:                                          # lower_left: text to the upper right of the point
                rx, ry = px_ + 2.0, py_ - h - 2.0
            if rx > wdev or rx + w < 0 or ry > hdev or ry + h < 0:
                continue
            if _ex is not None and (rx > _ex.right() or rx + w < _ex.left() or ry > _ex.bottom() or ry + h < _ex.top()):
                continue
            p.drawImage(QtCore.QPointF(float(int(rx)), float(int(ry))), self._label_image(lb, dpr))
            n += 1
        if self._note:
            p.setFont(self._f_small)
            p.setPen(QtGui.QColor(255, 200, 80))
            p.setBrush(QtGui.QColor(0, 0, 0, 140))
            p.drawRoundedRect(QtCore.QRectF(8.0, 8.0, 12.0 + 7.0 * len(self._note), 18.0), 3.0, 3.0)
            p.drawText(QtCore.QRectF(14.0, 9.0, 7.0 * len(self._note) + 4.0, 16.0), flags, self._note)
        self._painted = n
        p.restore()


# ---------------------------------------------------------------------------- the per-window overlay state
class HlhOverlay:
    """Feeds, computed periods and per-canvas geometry caches -- one instance per terminal window, shared by
    both canvases (they differ only in xmap)."""

    def __init__(self):
        self.feeds: Dict[str, HlhFeed] = {}
        self._res: Dict[Tuple[bool, int], tuple] = {}     # (is_week, key) -> (ver, hv, PeriodResult, cand_secs)
        self._split: Dict[str, tuple] = {}                # tf -> (rev, [(key, Candles)])
        self._geom: Dict[tuple, tuple] = {}               # (canvas, is_week, key) -> (ver, xkey, bloc_only, dark, [(xlo, xhi, pic)], labels)
        self._out: Dict[str, tuple] = {}                  # canvas -> (sig, (pics, labels, note))
        self._t_periods = 0.0
        self._periods_cache: List[tuple] = []
        self._periods_sig = None

    # -------------------------------------------------------------- params / floors
    @staticmethod
    def params() -> H.Params:
        c = config
        return H.Params(rows=int(c.HLH_ROWS), width_pct=int(c.HLH_WIDTH_PCT), low_max_pct=float(c.HLH_LOW_MAX_PCT),
                        high_min_pct=float(c.HLH_HIGH_MIN_PCT), shared_pct=float(c.HLH_SHARED_PCT),
                        do_merge=bool(c.HLH_MERGE), do_uncov=bool(c.HLH_UNCOVERED), show_d=True, show_dn=True,
                        max_ds=int(c.HLH_MAX_DS), va_pct=float(c.HLH_VA_PCT), tp_bin_min_day=int(c.HLH_TP_BIN_DAY_MIN),
                        tp_bin_min_week=int(c.HLH_TP_BIN_WEEK_MIN), tp_both=str(c.HLH_TP_BOTH), tz=str(c.HLH_TZ))

    @staticmethod
    def day_floor(now: float) -> float:
        """00:00 (HLH_TZ) of the oldest of HLH_DAYS complete day periods ending today."""
        z = H._zone(config.HLH_TZ)
        d = datetime.fromtimestamp(float(now), z)
        mid = datetime(d.year, d.month, d.day, tzinfo=z) - timedelta(days=max(0, int(config.HLH_DAYS) - 1))
        return mid.timestamp()

    @staticmethod
    def week_floor(now: float) -> float:
        return H.period_window(float(now), True, config.HLH_TZ)[0]

    def floor(self, week_on: bool, now: float) -> float:
        """Memoised per minute: the floors move once a day, the datetime work is ~15 us."""
        k = (int(now) // 60, bool(week_on))
        c = getattr(self, "_floor_memo", None)
        if c is not None and c[0] == k:
            return c[1]
        f = self.day_floor(now)
        if week_on:
            f = min(f, self.week_floor(now))
        self._floor_memo = (k, f)
        return f

    # -------------------------------------------------------------- feeds
    def ensure_feeds(self, week_on: bool, now: float) -> None:
        """Once a second (or when a toggle moved -- the terminal clears _feeds_k): start / re-floor / stop
        the klines feeds."""
        k = (int(now), bool(week_on))
        if getattr(self, "_feeds_k", None) == k:
            return
        self._feeds_k = k
        want = {config.HLH_DAY_TF: self.day_floor(now)}
        if week_on:
            wf = self.week_floor(now)
            if config.HLH_WEEK_TF == config.HLH_DAY_TF:
                want[config.HLH_DAY_TF] = min(want[config.HLH_DAY_TF], wf)
            else:
                want[config.HLH_WEEK_TF] = wf
        for tf, fl in want.items():
            f = self.feeds.get(tf)
            if f is None:
                f = HlhFeed(tf, fl, poll_secs=float(config.HLH_POLL_SECS))
                self.feeds[tf] = f
                f.start()
            else:
                if abs(f.start_ts - fl) > 1.0:
                    f.set_start(fl)
                if not f.alive():
                    f.start()
        for tf in list(self.feeds):
            if tf not in want:
                self.feeds.pop(tf).stop()

    def stop(self) -> None:
        for f in self.feeds.values():
            f.stop()
        self.feeds.clear()

    def note(self, week_on: bool) -> Optional[str]:
        tfs = [config.HLH_DAY_TF] + ([config.HLH_WEEK_TF] if week_on else [])
        for tf in tfs:
            f = self.feeds.get(tf)
            if f is None:
                return "HLH: feed starting"
            if not f.loaded:
                return ("HLH: %s klines unavailable -- %s" % (tf, f.error)) if f.error else ("HLH: loading %s klines" % tf)
        return None

    # -------------------------------------------------------------- periods
    def _split_for(self, tf: str, is_week: bool):
        f = self.feeds.get(tf)
        if f is None:
            return []
        cd = f.snapshot()
        if cd is None:
            return []
        k = (tf, is_week)
        cur = self._split.get(k)
        if cur is not None and cur[0] == f.rev:
            return cur[1]
        sp = H.split_periods(cd, is_week, config.HLH_TZ)
        self._split[k] = (f.rev, sp)
        return sp

    def periods(self, week_on: bool, now: float, force: bool = False) -> List[tuple]:
        """[(is_week, key, ver, PeriodResult, cand_secs)] -- a finished period computed once, the forming
        one recomputed when its candles changed; the whole thing gated by the feeds' revs."""
        sig = tuple((tf, f.rev) for tf, f in sorted(self.feeds.items())) + (bool(week_on),)
        if not force and sig == self._periods_sig:
            return self._periods_cache            # no feed moved -> nothing to recompute (revs move per poll)
        if not force and now - self._t_periods < float(config.HLH_RECALC_SECS) and self._periods_sig is not None:
            return self._periods_cache            # a rev moved, but not this often: next tick
        self._periods_sig = sig
        self._t_periods = now
        p = self.params()
        out = []
        kinds = [(False, config.HLH_DAY_TF)] + ([(True, config.HLH_WEEK_TF)] if week_on else [])
        live = set()
        for is_week, tf in kinds:
            cand_secs = float(config.TF_SECONDS.get(tf, 60))
            for key, pc in self._split_for(tf, is_week):
                ck = (is_week, key)
                live.add(ck)
                hv = (len(pc), float(pc.t[-1]), round(float(pc.v.sum()), 6), float(pc.c[-1]),
                      float(pc.h.max()), float(pc.l.min()))
                cur = self._res.get(ck)
                if cur is None or cur[1] != hv:
                    res = H.compute_period(pc, is_week, p, key)
                    ver = (cur[0] + 1) if cur else 1
                    cur = (ver, hv, res, cand_secs)
                    self._res[ck] = cur
                if cur[2] is not None:
                    out.append((is_week, key, cur[0], cur[2], cur[3]))
        for ck in [k for k in self._res if k not in live]:
            del self._res[ck]
        self._periods_cache = out
        return out

    # -------------------------------------------------------------- geometry
    def build(self, canvas: str, xmap, bloc_only: bool, dark: bool, week_on: bool, now: float,
              skip_before: float = -np.inf):
        """(pics, labels, note) for one canvas. Returns the SAME tuple object while nothing changed, so a
        caller can gate set_pics/set_labels on identity."""
        pers = self.periods(week_on, now)
        xkey = tuple(xmap.key)
        sig = (tuple((pk[0], pk[1], pk[2]) for pk in pers), xkey, bool(bloc_only), bool(dark), self.note(week_on), float(skip_before))
        cur = self._out.get(canvas)
        if cur is not None and cur[0] == sig:
            return cur[1]
        pics = []
        labels: List[Label] = []
        keep = set()
        for is_week, key, ver, res, cand_secs in pers:
            if res.per_end < skip_before:
                continue
            gk = (canvas, is_week, key)
            keep.add(gk)
            g = self._geom.get(gk)
            if g is None or g[0] != ver or g[1] != xkey or g[2] != bool(bloc_only) or g[3] != bool(dark):
                pl, lbl = build_period(res, xmap, cand_secs, bool(bloc_only), bool(dark))
                g = (ver, xkey, bool(bloc_only), bool(dark), pl, lbl)
                self._geom[gk] = g
            pics.extend(g[4])
            labels.extend(g[5])
        for gk in [k for k in self._geom if k[0] == canvas and k not in keep]:
            del self._geom[gk]
        out = (pics, labels, self.note(week_on))
        self._out[canvas] = (sig, out)
        return out

    def forget_canvas(self, canvas: str) -> None:
        """The canvas's items were destroyed (a mode switch): its next build must set content again."""
        self._out.pop(canvas, None)
