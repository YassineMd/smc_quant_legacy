"""Paper-trading account for the position tool's live-trade simulation.

Account model (the user's spec): $200,000 start balance; each trade risks 10% of the CURRENT balance as
MARGIN and applies 10x LEVERAGE, so notional exposure = margin * 10 (a fresh $200k account trades $200k of
SOL on $20k margin). The balance COMPOUNDS as simulated trades close and persists to
data/paper_account.json. Fees: Binance USDT-M taker 0.05% per side, charged on notional at entry AND exit
(realistic + conservative — TP would usually be a cheaper maker fill, so this never over-states the edge).

All money math lives here (pure, headless-testable); the PositionBracket renders it and the terminal feeds
it the live price each frame.
"""
from __future__ import annotations

import json
import os

from . import config

START_BALANCE = 200000.0
RISK_FRAC = 0.10          # MARGIN per trade = this * balance
LEVERAGE = 10.0
FEE_RATE = 0.0005         # 0.05% taker, charged per side (entry + exit)


class PaperAccount:
    def __init__(self, path: str = None, start: float = START_BALANCE):
        self.path = path or os.path.join(config.DATA_DIR, "paper_account.json")
        self.risk_frac = RISK_FRAC
        self.leverage = LEVERAGE
        self.fee_rate = FEE_RATE
        self.start = start
        self.balance = start
        self._load()

    # -- persistence (best-effort) --
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.balance = float(json.load(f).get("balance", self.start))
        except Exception:
            pass

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"balance": self.balance}, f)
        except Exception:
            pass

    def reset(self, start: float = None) -> None:
        self.balance = self.start if start is None else start
        self._save()

    # -- trade lifecycle --
    def open(self, entry: float, side: int) -> dict:
        """Size a new position off the CURRENT balance. side = +1 long / -1 short. Returns the open dict
        (nothing is charged to the balance until close)."""
        margin = max(0.0, self.balance) * self.risk_frac
        notional = margin * self.leverage
        qty = (notional / entry) if entry > 0 else 0.0            # SOL units controlled
        entry_fee = notional * self.fee_rate
        return {"entry": entry, "side": int(side), "margin": margin,
                "notional": notional, "qty": qty, "entry_fee": entry_fee}

    def live_pnl(self, pos: dict, price: float) -> tuple:
        """(net_usd, pct_on_margin) if the position were closed at ``price`` right now — both fees included."""
        gross = pos["qty"] * (price - pos["entry"]) * pos["side"]
        exit_fee = pos["qty"] * price * self.fee_rate
        net = gross - pos["entry_fee"] - exit_fee
        pct = (net / pos["margin"] * 100.0) if pos["margin"] > 0 else 0.0
        return net, pct

    def close(self, pos: dict, exit_price: float) -> dict:
        """Realize the position at ``exit_price``, credit/debit the balance, persist. Returns the result."""
        net, pct = self.live_pnl(pos, exit_price)
        self.balance += net
        self._save()
        return {"net": net, "pct": pct, "balance": self.balance, "exit": exit_price}
