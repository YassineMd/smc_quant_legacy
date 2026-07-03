# S5d-GRID — exit-geometry matrix on the S5d fires

_**Characterization on spent tape: this grid is a MAP, not a decision — any preferred cell is a pre-registered FORWARD hypothesis. Multiplicity +32 noted -> program counter 498.** Inputs = the committed S5d fire sets (26/51 long, 12/24 short; no re-detection). S1 walker conventions; fires overlap in time (independent sims). TRAIL = arm +0.5%, exit on a 0.3% retrace off the post-arm extreme; retrace is judged against the extreme up to the PREVIOUS bar (no look-ahead); the arming bar's own-bar retrace exits at arm-0.3% and is flagged ambiguous; armed-at-cap = UNRESOLVED. Values below = NET expectancy %/trade at taker 0.10% RT; matrices in fire counts n, resolved varies by cell. _Italic_ = cell rests on < 20 resolved trades (counts only)._

## LOCKED LONG — 26 fires (net expectancy %/trade; **bold** = best, † = current geometry SL 0.30 / TP 0.5)

| SL \ exit | TP0.5 | TP0.75 | TP1.0 | TRAIL |
|---|---|---|---|---|
| 0.20 | -0.058 | -0.117 | -0.069 | -0.063 |
| 0.25 | -0.033 | -0.119 | -0.062 | -0.051 |
| 0.30 | **-0.031** † | -0.117 | -0.100 | -0.046 |
| 0.40 | -0.050 | -0.146 | -0.123 | -0.074 |

Best cell SL 0.30 / TP0.5: win 46.2% (res 26/26, unres 0, eod 0, ambig 0), avgW +0.500 / avgL -0.300, gross +0.069% -> net -0.031%, med res 11.5 min.

Robustness: same cell in UNLOCKED = -0.055% (res 51); regime halves: pre +0.080% (res 5) / post -0.057% (res 21). NOT uniform — carried by a slice; treat as tape-specific until forward data.

## UNLOCKED LONG — 51 fires (net expectancy %/trade; **bold** = best, † = current geometry SL 0.30 / TP 0.5)

| SL \ exit | TP0.5 | TP0.75 | TP1.0 | TRAIL |
|---|---|---|---|---|
| 0.20 | -0.067 | -0.132 | -0.112 | -0.082 |
| 0.25 | **-0.026** | -0.115 | -0.100 | -0.047 |
| 0.30 | -0.055 † | -0.132 | -0.114 | -0.076 |
| 0.40 | -0.112 | -0.184 | -0.192 | -0.133 |

Best cell SL 0.25 / TP0.5: win 43.1% (res 51/51, unres 0, eod 0, ambig 0), avgW +0.500 / avgL -0.250, gross +0.074% -> net -0.026%, med res 13.0 min.

Robustness: same cell in LOCKED = -0.033% (res 26); regime halves: pre -0.145% (res 11) / post +0.006% (res 40). NOT uniform — carried by a slice; treat as tape-specific until forward data.

## LOCKED SHORT — 12 fires (net expectancy %/trade; **bold** = best, † = current geometry SL 0.30 / TP 0.5)

| SL \ exit | TP0.5 | TP0.75 | TP1.0 | TRAIL |
|---|---|---|---|---|
| 0.20 | _-0.125_ | **_-0.062_** | _-0.200_ | _-0.102_ |
| 0.25 | _-0.163_ | _-0.100_ | _-0.236_ | _-0.140_ |
| 0.30 | _-0.200_ † | _-0.138_ | _-0.282_ | _-0.177_ |
| 0.40 | _-0.275_ | _-0.213_ | _-0.373_ | _-0.252_ |

Best cell SL 0.20 / TP0.75: win 25.0% (res 12/12, unres 0, eod 0, ambig 0), avgW +0.750 / avgL -0.200, gross +0.037% -> net -0.062%, med res 4.2 min.

Robustness: same cell in UNLOCKED = -0.023% (res 24); regime halves: pre +0.175% (res 4) / post -0.181% (res 8). NOT uniform — carried by a slice; treat as tape-specific until forward data.

## UNLOCKED SHORT — 24 fires (net expectancy %/trade; **bold** = best, † = current geometry SL 0.30 / TP 0.5)

| SL \ exit | TP0.5 | TP0.75 | TP1.0 | TRAIL |
|---|---|---|---|---|
| 0.20 | -0.067 | -0.023 | -0.150 | -0.060 |
| 0.25 | -0.100 | -0.058 | -0.187 | -0.093 |
| 0.30 | -0.067 † | **+0.038** | -0.174 | -0.065 |
| 0.40 | -0.088 | +0.027 | -0.196 | -0.071 |

Best cell SL 0.30 / TP0.75: win 41.7% (res 24/24, unres 0, eod 0, ambig 0), avgW +0.750 / avgL -0.300, gross +0.138% -> net +0.038%, med res 22.0 min.

Robustness: same cell in LOCKED = -0.138% (res 12); regime halves: pre +0.183% (res 9) / post -0.050% (res 15). NOT uniform — carried by a slice; treat as tape-specific until forward data.

## Winner-clip — the cost of tightening the SL
Current-geometry (SL 0.30 / TP 0.5) winners stopped by a tighter SL BEFORE reaching +0.5, path-sequenced per fire (same-bar both -> clipped, S1 rule):

| cell | winners | clipped @0.20 | @0.25 | @0.40 |
|---|---|---|---|---|
| LOCKED-long | 12 | 3 | 1 | 0 |
| UNLOCKED-long | 22 | 5 | 0 | 0 |
| LOCKED-short | 3 | 0 | 0 | 0 |
| UNLOCKED-short | 10 | 2 | 2 | 0 |

## Honest flags
- 32 cells on 26-51 (long) / 12-24 (short) fires is a MINED grid: it locates the trade-off, it cannot confirm a cell. Only forward tape can.
- The trail's conservative sequencing UNDERSTATES trail exits (same-bar new-extreme retraces credit the previous extreme).
- Locked-short cells all rest on 12 fires -> counts only throughout.

## HARD STOP
No further grids, no threshold variants beyond this spec.
