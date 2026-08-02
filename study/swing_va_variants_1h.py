"""1h ONLY — sub-hypotheses on the Price&CVD-Swings VA-zone reversal, each vs the SAME placebo null (random-offset
levels). Answers whether ANY narrower slice of the zone is genuine S/R that beats an arbitrary nearby level:

  band all       — the full buy/sell-POC/LVN band, every first touch (the headline test, recap)
  band aligned   — band tested only in its DESIGNATED role: up-leg zone as SUPPORT (approach from above),
                   down-leg zone as RESISTANCE (from below) — the canonical retrace-into-zone setup
  buy-POC / sell-POC / LVN — each single line as a level, on its own
  (LVN also reported as BREAK% — the 'low-volume node = price accelerates through' hypothesis)

Reversal = symmetric first-passage: from the level/band mid, price reaches the APPROACH-side barrier (+/-D) before the
FAR side. EDGE = real - placebo (pp). Causal (only after the pivot's confirm bar). 1h recon.

CLI: python study/swing_va_variants_1h.py
"""
import os, sys, random
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.swing_va_reversal import load_tf, first_touch, resolve, binom_p
from app import swing_lvn_detect as SL

random.seed(4242)
MAXSCAN = 250
K = 48


def build_zones(A):
    r = SL._dev_leg(A)
    H, L, C, thr, piv, dev = r
    Z = []
    for k in range(1, len(piv)):
        b0 = int(piv[k - 1][0]); b1 = int(piv[k][0]); cb = int(piv[k][3]); eh = bool(piv[k][2])
        if b1 <= b0:
            continue
        try:
            va = SL.va_lines(A, b0, b1)
        except Exception:
            va = None
        if not va:
            continue
        Z.append(dict(bp=va.get("buy_poc"), sp=va.get("sell_poc"), lvn=va.get("lvn"), cb=cb, eh=eh))
    return Z, thr


def specs_band(Z):
    out = []
    for z in Z:
        lv = [z[x] for x in ("bp", "sp", "lvn") if z[x] is not None]
        if len(lv) < 2:
            continue
        lo, hi = min(lv), max(lv)
        if hi <= lo:
            hi = lo * (1.0 + 1e-4)
        out.append((lo, hi, z["cb"], z["eh"]))
    return out


def specs_line(Z, key):
    out = []
    for z in Z:
        v = z[key]
        if v is None:
            continue
        out.append((v * (1.0 - 1e-4), v * (1.0 + 1e-4), z["cb"], z["eh"]))   # a hair of width so first_touch works
    return out


def evaluate(A, specs, D, filt=None, nplac=5):
    H = [float(b.get("high", 0.0) or 0.0) for b in A]
    L = [float(b.get("low", 0.0) or 0.0) for b in A]
    C = [b["close"] for b in A]

    def test(shift):
        rev = brk = un = 0
        for (zlo, zhi, cb, eh) in specs:
            zl, zh = zlo * (1.0 + shift), zhi * (1.0 + shift)
            ft = first_touch(H, L, C, cb, zl, zh, MAXSCAN)
            if ft is None:
                continue
            t, fa = ft
            if filt is not None and not filt(eh, fa):
                continue
            v = resolve(H, L, t, zl, zh, fa, D, K)
            if v is None:
                un += 1; continue
            if v == "rev":
                rev += 1
            else:
                brk += 1
        return rev, brk

    rr, rb = test(0.0); rres = rr + rb
    prev = pres = 0
    for _ in range(nplac):
        s = random.uniform(0.01, 0.03) * random.choice((-1, 1))
        pr, pb = test(s); prev += pr; pres += pr + pb
    prate = (prev / pres) if pres else 0.0
    real = (rr / rres) if rres else 0.0
    p = binom_p(rr, rres, prate) if rres else 1.0
    return dict(n=rres, real=real, plac=prate, edge=(real - prate) * 100.0, p=p)


ALIGNED = lambda eh, fa: (eh and fa) or ((not eh) and (not fa))   # up-leg=support(from above) / down-leg=resistance(from below)


def row(name, res):
    verdict = "<-- beats placebo" if (res["edge"] >= 3 and res["p"] < 0.05) else ""
    print("  %-16s n=%-5d  reversal %5.1f%%   placebo %5.1f%%   edge %+5.1f pp   p=%.3f  %s"
          % (name, res["n"], 100 * res["real"], 100 * res["plac"], res["edge"], res["p"], verdict))


def main():
    A = load_tf("1h")
    Z, thr = build_zones(A)
    sb = specs_band(Z)
    print("1h recon: %d candles | ZigZag thr=%.2f%% | swing zones=%d | K=%d" % (len(A), thr * 100, len(sb), K))
    for D in (0.01, 0.015):
        print("\n=== D = %.1f%% (symmetric barrier) ===" % (D * 100))
        row("band all", evaluate(A, sb, D))
        row("band aligned", evaluate(A, sb, D, filt=ALIGNED))
        row("buy-POC", evaluate(A, specs_line(Z, "bp"), D))
        row("sell-POC", evaluate(A, specs_line(Z, "sp"), D))
        r_lvn = evaluate(A, specs_line(Z, "lvn"), D)
        row("LVN", r_lvn)
        print("      (LVN break-through: real %.1f%% vs placebo %.1f%%  -> break edge %+.1f pp)"
              % (100 * (1 - r_lvn["real"]), 100 * (1 - r_lvn["plac"]), -r_lvn["edge"]))


if __name__ == "__main__":
    main()
