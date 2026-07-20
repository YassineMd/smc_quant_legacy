"""Build data + metrics + Monte Carlo + charts for the MMXSKEW / MMXSKEW-ORB PDF report.
Outputs study/out/report_metrics.json and study/out/rep_*.png. Run:  python study/report_build.py
"""
from __future__ import annotations
import os, sys, json, math, statistics, datetime as dt
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_rr_sweep as RR
import study.mm_skew_orb as ORB
import study.mm_skew_strategy as S

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
BAL0 = 200000.0
FEE = 0.0008
RRV = 1.5
np.random.seed(7)

# palette
GREEN, RED, TEAL, NAVY, GRAY, GRID = "#16a34a", "#dc2626", "#0ea5e9", "#1e293b", "#64748b", "#e2e8f0"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRAY,
                     "axes.linewidth": 0.8, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})


def build():
    M, span = P.build()
    for i in range(len(M)):
        b = M[i]; cv = float(b.get("curr_vol", 0)) or 1.0
        b["delta"] = (float(b.get("buy_vol", 0)) - float(b.get("sell_vol", 0))) / cv * 100
        b["dtt"] = dt.datetime.utcfromtimestamp(b.get("start_time", 0))
        u = b["dtt"]; et = u - dt.timedelta(hours=4)
        b["etdate"] = et.date(); b["utcmin"] = u.hour * 60 + u.minute
    return M, span


def sigf(b):
    s = P.sig(b)
    return 0 if (s == 1 and b["delta"] >= 15) else s


def v11_trades(M):
    i = 0; T = []
    while i < len(M) - 1:
        s = sigf(M[i])
        if s == 0:
            i += 1; continue
        res = RR.simulate_rr(M, i, s, RRV, "sl")
        if res is None:
            i += 1; continue
        o, rf, jc = res
        T.append(dict(ei=i, xi=jc, side=s, entry=M[i]["c"], retf=rf, win=(o == "TP"),
                      dur=jc - i, et=M[i]["dtt"], xt=M[jc]["dtt"]))
        i = jc + 1
    return T


def orb_trades(M):
    entries, _ = ORB.daily_entries(M, True)
    T = []; last = -1
    for i, side, so in entries:
        if i <= last:
            continue
        res = ORB.sim(M, i, side, RRV, "orb", so)
        if res is None:
            continue
        o, rf, jc = res
        T.append(dict(ei=i, xi=jc, side=side, entry=M[i]["c"], retf=rf, win=(o == "TP"),
                      dur=jc - i, et=M[i]["dtt"], xt=M[jc]["dtt"]))
        last = jc
    return T


def metrics(T):
    rets = np.array([t["retf"] for t in T]); nets = rets - FEE
    n = len(T); wins = rets[rets > 0]; losses = rets[rets <= 0]
    bal = BAL0; curve = [BAL0]; peak = BAL0; mdd = 0.0
    for r in nets:
        bal *= (1 + r); curve.append(bal); peak = max(peak, bal); mdd = max(mdd, (peak - bal) / peak)
    gp = wins.sum(); gl = abs(losses.sum())
    # streaks
    ws = ls = wmax = lmax = 0
    for t in T:
        if t["win"]:
            ws += 1; ls = 0; wmax = max(wmax, ws)
        else:
            ls += 1; ws = 0; lmax = max(lmax, ls)
    m = dict(
        n=n, wins=int((rets > 0).sum()), losses=int((rets <= 0).sum()),
        win_rate=100 * (rets > 0).mean(),
        net_profit=bal - BAL0, net_pct=(bal / BAL0 - 1) * 100, final_bal=bal,
        avg_win=float(wins.mean() * 100) if len(wins) else 0.0,
        avg_loss=float(losses.mean() * 100) if len(losses) else 0.0,
        profit_factor=float(gp / gl) if gl > 0 else float("inf"),
        expectancy=float(nets.mean() * 100),
        payoff=float((wins.mean() / abs(losses.mean()))) if len(wins) and len(losses) else 0.0,
        max_dd=mdd * 100,
        sharpe=float(nets.mean() / nets.std(ddof=0) * math.sqrt(n)) if n > 1 and nets.std() > 0 else 0.0,
        best=float(rets.max() * 100), worst=float(rets.min() * 100),
        avg_dur=float(np.mean([t["dur"] for t in T])),
        win_streak=wmax, loss_streak=lmax,
        avg_win_usd=float(wins.mean() * BAL0) if len(wins) else 0.0,
        avg_loss_usd=float(losses.mean() * BAL0) if len(losses) else 0.0,
    )
    return m, curve


def montecarlo(T, sims=20000):
    r = np.array([t["retf"] - FEE for t in T]); n = len(r)
    idx = np.random.randint(0, n, size=(sims, n)); samp = r[idx]
    finals = (np.prod(1 + samp, axis=1) - 1) * 100
    perm = np.array([np.random.permutation(r) for _ in range(sims)])
    path = np.cumprod(1 + perm, axis=1); peak = np.maximum.accumulate(path, axis=1)
    dd = np.max((peak - path) / peak, axis=1) * 100
    return dict(finals=finals, p_profit=float((finals > 0).mean() * 100),
                p5=float(np.percentile(finals, 5)), p50=float(np.percentile(finals, 50)),
                p95=float(np.percentile(finals, 95)), dd_p95=float(np.percentile(dd, 95)),
                dd_med=float(np.percentile(dd, 50)))


# ---------- charts ----------
def chart_equity(curve, T, color, fn):
    fig, ax = plt.subplots(figsize=(7.4, 2.7))
    x = [T[0]["et"]] + [t["xt"] for t in T]
    ax.plot(x, curve, color=color, lw=2.0)
    ax.fill_between(x, BAL0, curve, color=color, alpha=0.08)
    ax.axhline(BAL0, color=GRAY, lw=0.8, ls="--")
    ax.set_ylabel("Equity ($)"); ax.yaxis.set_major_formatter(lambda v, _: f"${v/1000:.0f}k")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.margins(x=0.01)
    fig.tight_layout(pad=0.4); fig.savefig(fn, bbox_inches="tight"); plt.close(fig)


def chart_trades(M, T, fn):
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    xs = [b["dtt"] for b in M]; ys = [b["c"] for b in M]
    ax.plot(xs, ys, color=NAVY, lw=0.8, alpha=0.55)
    for t in T:
        c = GREEN if t["win"] else RED
        mk = "^" if t["side"] > 0 else "v"
        ax.scatter(t["et"], t["entry"], marker=mk, s=46, color=c, edgecolor="white", lw=0.6, zorder=3)
    ax.set_ylabel("SOL price ($)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    from matplotlib.lines import Line2D
    leg = [Line2D([0], [0], marker="^", color="w", markerfacecolor=GREEN, markersize=9, label="Win"),
           Line2D([0], [0], marker="v", color="w", markerfacecolor=RED, markersize=9, label="Loss")]
    ax.legend(handles=leg, loc="upper left", frameon=False, fontsize=8)
    ax.margins(x=0.01)
    fig.tight_layout(pad=0.4); fig.savefig(fn, bbox_inches="tight"); plt.close(fig)


def chart_mc(mc, color, fn):
    fig, ax = plt.subplots(figsize=(7.4, 2.7))
    ax.hist(mc["finals"], bins=60, color=color, alpha=0.6, edgecolor="white", lw=0.3)
    ax.axvline(0, color=RED, lw=1.2, ls="--", label="break-even")
    ax.axvline(mc["p50"], color=NAVY, lw=1.4, label=f"median {mc['p50']:+.1f}%")
    ax.axvspan(mc["p5"], mc["p95"], color=color, alpha=0.10, label=f"5–95%: {mc['p5']:+.0f}…{mc['p95']:+.0f}%")
    ax.set_xlabel("Simulated 28-day return (%)"); ax.set_ylabel("frequency")
    ax.legend(loc="upper right", frameon=False, fontsize=8)
    fig.tight_layout(pad=0.4); fig.savefig(fn, bbox_inches="tight"); plt.close(fig)


def main():
    os.makedirs(OUT, exist_ok=True)
    M, span = build()
    out = {"span": span, "bal0": BAL0, "rr": RRV, "generated_utc": M[-1]["dtt"].strftime("%Y-%m-%d")}
    for key, T, color in (("MMXSKEW", v11_trades(M), TEAL), ("MMXSKEW-ORB", orb_trades(M), GREEN)):
        m, curve = metrics(T); mc = montecarlo(T)
        pre = "repA" if key == "MMXSKEW" else "repB"
        chart_equity(curve, T, color, os.path.join(OUT, pre + "_equity.png"))
        chart_trades(M, T, os.path.join(OUT, pre + "_trades.png"))
        chart_mc(mc, color, os.path.join(OUT, pre + "_mc.png"))
        mc_json = {k: v for k, v in mc.items() if k != "finals"}
        out[key] = dict(metrics=m, mc=mc_json, prefix=pre,
                        span_days=(T[-1]["xt"] - T[0]["et"]).days,
                        first=T[0]["et"].strftime("%b %d"), last=T[-1]["xt"].strftime("%b %d"))
        print(f"{key}: n={m['n']} win={m['win_rate']:.1f}% net={m['net_pct']:+.1f}% "
              f"PF={m['profit_factor']:.2f} DD={m['max_dd']:.1f}% Pprofit={mc['p_profit']:.0f}%")
    with open(os.path.join(OUT, "report_metrics.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("charts + report_metrics.json written to study/out/")


if __name__ == "__main__":
    main()
