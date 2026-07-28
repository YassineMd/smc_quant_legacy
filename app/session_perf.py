"""Lightweight, default-on TERMINAL session profiler for the client-side progressive-lag hunt.

Appends one CSV row per ~10s window to ``data/session_perf.log``: frame-time p50/p99/max over the window,
process RSS, the Qt scene item count, the session-living cache/collection sizes (heatmap columns, trade
bubbles, closed buckets, drawings), and the top-3 most expensive draw sections — so the accumulator behind
the lag is NAMED BY DATA, not guessed. Negligible overhead: a handful of ``perf_counter`` calls per 20 Hz
frame + one CSV append every 10 s. Pure client instrumentation — touches NOTHING the daemon or capture owns.
"""
from __future__ import annotations

import csv
import os
import time
from collections import defaultdict, deque
from typing import Dict


def rss_mb() -> float:
    """Resident set size in MB. psutil if present, else the Windows psapi call via ctypes (no dep), else 0."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024.0 * 1024.0)
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.windll.kernel32; psapi = ctypes.windll.psapi
        # argtypes/restype MUST be set or the 64-bit HANDLE from GetCurrentProcess is truncated -> call fails.
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        pmc = _PMC(); pmc.cb = ctypes.sizeof(_PMC)
        if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:
        pass
    return 0.0


_COLS = ["ts", "mode", "uptime_s", "frames", "frame_p50_ms", "frame_p99_ms", "frame_max_ms",
         "rss_mb", "scene_items", "hm_cols", "hm_cols_kb", "hm_bubbles", "hm_bubbles_kb",
         "closed_buckets", "draw_items", "extra", "top3_draw"]


class MemTracer:
    """tracemalloc leak-hunt. After a short warm-up (so one-time startup allocations settle) it fixes a BASELINE
    snapshot, then every ``interval_s`` appends the top allocation sites that GREW vs that baseline — so what remains
    in the log IS the accumulator, named by file:line and its call chain. DIAGNOSTIC ONLY: tracemalloc roughly doubles
    allocation cost and inflates RSS with its own bookkeeping (read the ``traced=`` MB, not process RSS, for the true
    app figure) — gate behind config.SESSION_MEMTRACE and turn it OFF once the leak is named. Never raises."""

    def __init__(self, log_path: str, nframes: int = 6, baseline_after_s: float = 45.0,
                 interval_s: float = 30.0, top: int = 12):
        self.log_path = log_path
        self.nframes = nframes
        self.baseline_after_s = baseline_after_s
        self.interval_s = interval_s
        self.top = top
        self._t0 = time.perf_counter()
        self._last = 0.0
        self._baseline = None
        self._ok = False
        try:
            import tracemalloc
            self._tm = tracemalloc
            tracemalloc.start(nframes)
            self._ok = True
            with open(self.log_path, "a") as f:
                f.write("\n==== MemTracer start (nframes=%d, baseline@+%.0fs, every %.0fs) — %s ====\n"
                        % (nframes, baseline_after_s, interval_s, time.strftime("%Y-%m-%d %H:%M:%S")))
        except Exception:
            self._ok = False

    def _uptime(self) -> float:
        return time.perf_counter() - self._t0

    def _snapshot(self):
        tm = self._tm
        return tm.take_snapshot().filter_traces((
            tm.Filter(False, "<frozen importlib._bootstrap>"),
            tm.Filter(False, "<frozen importlib._bootstrap_external>"),
            tm.Filter(False, tm.__file__),
            tm.Filter(False, __file__),
        ))

    def maybe_snapshot(self) -> None:
        """Call every profiler flush (~10s); it self-throttles to ``interval_s`` and self-baselines after warm-up."""
        if not self._ok:
            return
        try:
            up = self._uptime()
            if up - self._last < self.interval_s:
                return
            self._last = up
            snap = self._snapshot()
            cur, peak = self._tm.get_traced_memory()
            if self._baseline is None:
                if up >= self.baseline_after_s:
                    self._baseline = snap
                    self._append("BASELINE @ +%.0fs  traced=%.1fMB peak=%.1fMB" % (up, cur / 1e6, peak / 1e6), [])
                return
            diffs = self._snapshot_compare(snap)
            growers = [d for d in diffs if d.size_diff > 0][:self.top]
            lines = []
            for d in growers:
                lines.append("  +%9.1f KB  %+7d blocks  (now %.1f KB / %d blocks)"
                             % (d.size_diff / 1024.0, d.count_diff, d.size / 1024.0, d.count))
                for fr in list(d.traceback)[::-1][:self.nframes]:   # allocation site first, then its callers upward
                    lines.append("        %s:%d" % (fr.filename, fr.lineno))
            self._append("t=+%.0fs  traced=%.1fMB peak=%.1fMB  |  top %d GROWERS vs baseline:"
                         % (up, cur / 1e6, peak / 1e6, len(growers)), lines)
        except Exception:
            pass

    def _snapshot_compare(self, snap):
        return snap.compare_to(self._baseline, "traceback")

    def _append(self, header: str, lines) -> None:
        try:
            with open(self.log_path, "a") as f:
                f.write("\n[" + time.strftime("%Y-%m-%d %H:%M:%S") + "] " + header + "\n")
                for ln in lines:
                    f.write(ln + "\n")
        except Exception:
            pass


class SessionProfiler:
    """Accumulates frame + per-section timings between ~10s flushes; each flush appends one CSV row built
    from the window's frame stats plus a caller-supplied accumulator ``sample`` dict. Never raises."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self._t0 = time.perf_counter()                              # session start (monotonic)
        self._frames: deque = deque()                              # frame times (ms) this window
        self._sections: Dict[str, list] = defaultdict(lambda: [0.0, 0])  # name -> [total_ms, count]

    def note_frame(self, ms: float) -> None:
        self._frames.append(ms)

    def note_section(self, name: str, ms: float) -> None:
        s = self._sections[name]; s[0] += ms; s[1] += 1

    def uptime_s(self) -> float:
        return time.perf_counter() - self._t0

    def flush(self, sample: Dict) -> None:
        try:
            fr = sorted(self._frames); n = len(fr)

            def q(p):
                return fr[min(n - 1, int(p * n))] if n else 0.0
            p50, p99, mx = q(0.5), q(0.99), (fr[-1] if n else 0.0)
            top = sorted(self._sections.items(), key=lambda kv: kv[1][0], reverse=True)[:3]
            top_str = " | ".join("%s:%.1fms/%dc" % (k, v[0], v[1]) for k, v in top)
            row = {
                "ts": int(time.time()), "mode": sample.get("mode", ""),
                "uptime_s": round(self.uptime_s(), 1), "frames": n,
                "frame_p50_ms": round(p50, 2), "frame_p99_ms": round(p99, 2), "frame_max_ms": round(mx, 2),
                "rss_mb": round(sample.get("rss_mb", 0.0), 1),
                "scene_items": sample.get("scene_items", 0),
                "hm_cols": sample.get("hm_cols", 0), "hm_cols_kb": sample.get("hm_cols_kb", 0),
                "hm_bubbles": sample.get("hm_bubbles", 0), "hm_bubbles_kb": sample.get("hm_bubbles_kb", 0),
                "closed_buckets": sample.get("closed_buckets", 0),
                "draw_items": sample.get("draw_items", 0),
                "extra": sample.get("extra", ""), "top3_draw": top_str,
            }
            new = not os.path.exists(self.log_path)
            with open(self.log_path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=_COLS)
                if new:
                    w.writeheader()
                w.writerow(row)
        except Exception:
            pass
        finally:
            self._frames.clear(); self._sections.clear()
