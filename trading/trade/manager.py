"""Trade Manager.

Manages the full lifecycle of trades generated from validated AI signals:
  - Open: create trade from validated signal
  - Update: check break-even, trailing stop, partial TP on every price tick
  - Close: manual close or automatic close on SL/TP hit
  - History: persist closed trades in memory (SQLite optional upgrade)
  - Performance: compute win rate, PnL, profit factor, drawdown
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from trading import config

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    id: str
    symbol: str
    direction: str          # "LONG" | "SHORT"
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None
    status: str             # "active" | "closed"
    opened_at: int          # unix timestamp
    setup_type: str
    confidence: float
    risk_reward: float

    # Live state
    current_price: float | None = None
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None

    # Manager state
    break_even_triggered: bool = False
    partial_tp_triggered: bool = False
    trailing_stop_active: bool = False
    trailing_stop_price: float | None = None

    # Close info
    closed_at: int | None = None
    close_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeManager:
    """In-memory trade manager."""

    def __init__(self) -> None:
        self._active: dict[str, Trade] = {}   # id -> Trade
        self._history: list[Trade] = []        # closed trades (newest first)

    # ── Open ──────────────────────────────────────────────────────────────────

    def open_trade(self, signal: dict[str, Any]) -> Trade:
        """Create and register a new trade from a validated signal."""
        trade = Trade(
            id=str(uuid.uuid4())[:8],
            symbol=signal["symbol"],
            direction=signal["signal"],
            entry=float(signal["entry"]),
            stop_loss=float(signal["stop_loss"]),
            take_profit_1=float(signal["take_profit_1"]),
            take_profit_2=(
                float(signal["take_profit_2"]) if signal.get("take_profit_2") else None
            ),
            status="active",
            opened_at=int(time.time()),
            setup_type=signal.get("setup_type", ""),
            confidence=float(signal.get("confidence", 0.0)),
            risk_reward=float(signal.get("risk_reward", 0.0)),
        )
        self._active[trade.id] = trade
        logger.info(
            "Trade opened [%s] %s %s @ %.4f  SL=%.4f  TP1=%.4f  RR=%.2f",
            trade.id,
            trade.symbol,
            trade.direction,
            trade.entry,
            trade.stop_loss,
            trade.take_profit_1,
            trade.risk_reward,
        )
        return trade

    # ── Update ─────────────────────────────────────────────────────────────────

    def update_price(
        self,
        symbol: str,
        price: float,
        atr_val: float = 0.0,
    ) -> list[Trade]:
        """Update all active trades for a symbol with the latest price.

        Handles:
        - Unrealized PnL calculation
        - SL/TP hit detection
        - Break-even trigger (when 1:1 RR hit)
        - Trailing stop activation and movement
        - Partial TP (close 50% at TP1)

        Returns list of trades that were closed in this update.
        """
        closed_this_tick: list[Trade] = []

        for trade in list(self._active.values()):
            if trade.symbol != symbol:
                continue

            trade.current_price = price
            sign = 1.0 if trade.direction == "LONG" else -1.0
            pnl_pct = (price - trade.entry) / trade.entry * sign * 100
            trade.unrealized_pnl = round(pnl_pct, 4)

            # Determine effective stop loss (trailing or original)
            effective_sl = trade.trailing_stop_price or trade.stop_loss

            # 1. Stop loss hit
            if (trade.direction == "LONG" and price <= effective_sl) or (
                trade.direction == "SHORT" and price >= effective_sl
            ):
                closed = self._close(trade, "stop_loss", price)
                closed_this_tick.append(closed)
                continue

            # 2. TP1 hit
            if (trade.direction == "LONG" and price >= trade.take_profit_1) or (
                trade.direction == "SHORT" and price <= trade.take_profit_1
            ):
                if not trade.partial_tp_triggered:
                    # Partial TP: record but keep trade open
                    trade.partial_tp_triggered = True
                    logger.info("Partial TP1 hit [%s] @ %.4f", trade.id, price)
                elif trade.take_profit_2 is None:
                    # Full close at TP1 if no TP2
                    closed = self._close(trade, "take_profit_1", price)
                    closed_this_tick.append(closed)
                    continue

            # 3. TP2 hit
            if (
                trade.take_profit_2 is not None
                and trade.partial_tp_triggered
                and (
                    (trade.direction == "LONG" and price >= trade.take_profit_2)
                    or (trade.direction == "SHORT" and price <= trade.take_profit_2)
                )
            ):
                closed = self._close(trade, "take_profit_2", price)
                closed_this_tick.append(closed)
                continue

            # 4. Break-even trigger (1:1 RR)
            risk = abs(trade.entry - trade.stop_loss)
            be_threshold = risk * config.BREAK_EVEN_TRIGGER_RR
            price_move = (price - trade.entry) * sign
            if not trade.break_even_triggered and price_move >= be_threshold:
                trade.stop_loss = trade.entry  # Move SL to entry
                trade.break_even_triggered = True
                logger.info("Break-even triggered [%s] SL moved to %.4f", trade.id, trade.entry)

            # 5. Trailing stop
            if trade.break_even_triggered and atr_val > 0:
                trail_level = price - sign * atr_val * config.TRAILING_STOP_ATR_MULT
                if not trade.trailing_stop_active:
                    trade.trailing_stop_active = True
                    trade.trailing_stop_price = trail_level
                elif trade.trailing_stop_price is not None:
                    # Only move in favourable direction
                    if (trade.direction == "LONG" and trail_level > trade.trailing_stop_price) or (
                        trade.direction == "SHORT" and trail_level < trade.trailing_stop_price
                    ):
                        trade.trailing_stop_price = trail_level

        return closed_this_tick

    # ── Close ─────────────────────────────────────────────────────────────────

    def close_trade(self, trade_id: str, current_price: float | None = None) -> Trade | None:
        """Manually close an active trade."""
        trade = self._active.get(trade_id)
        if not trade:
            return None
        price = current_price or trade.current_price or trade.entry
        return self._close(trade, "manual", price)

    def _close(self, trade: Trade, reason: str, price: float) -> Trade:
        sign = 1.0 if trade.direction == "LONG" else -1.0
        pnl_pct = (price - trade.entry) / trade.entry * sign * 100
        trade.realized_pnl = round(pnl_pct, 4)
        trade.unrealized_pnl = None
        trade.current_price = price
        trade.status = "closed"
        trade.close_reason = reason
        trade.closed_at = int(time.time())

        del self._active[trade.id]
        self._history.insert(0, trade)
        # Trim history
        if len(self._history) > config.MAX_TRADE_HISTORY:
            self._history = self._history[: config.MAX_TRADE_HISTORY]

        logger.info(
            "Trade closed [%s] %s %s reason=%s price=%.4f PnL=%.2f%%",
            trade.id,
            trade.symbol,
            trade.direction,
            reason,
            price,
            trade.realized_pnl,
        )
        return trade

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_active(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._active.values()]

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._history[:limit]]

    def get_performance(self) -> dict[str, Any]:
        closed = self._history
        if not closed:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_win": None,
                "avg_loss": None,
                "profit_factor": None,
                "max_drawdown": None,
                "avg_risk_reward": None,
                "best_trade": None,
                "worst_trade": None,
            }

        winners = [t for t in closed if (t.realized_pnl or 0) > 0]
        losers = [t for t in closed if (t.realized_pnl or 0) <= 0]

        total_pnl = sum(t.realized_pnl or 0 for t in closed)
        gross_win = sum(t.realized_pnl for t in winners if t.realized_pnl)
        gross_loss = abs(sum(t.realized_pnl for t in losers if t.realized_pnl))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

        # Running drawdown
        equity: list[float] = []
        cum = 0.0
        for t in reversed(closed):
            cum += t.realized_pnl or 0
            equity.append(cum)
        peak = 0.0
        max_dd = 0.0
        for e in equity:
            peak = max(peak, e)
            max_dd = max(max_dd, peak - e)

        pnl_list = [t.realized_pnl for t in closed if t.realized_pnl is not None]
        rr_list = [t.risk_reward for t in closed if t.risk_reward]

        return {
            "total_trades": len(closed),
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate": round(len(winners) / len(closed) * 100, 1) if closed else 0.0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(gross_win / len(winners), 2) if winners else None,
            "avg_loss": round(-gross_loss / len(losers), 2) if losers else None,
            "profit_factor": round(profit_factor, 2) if profit_factor else None,
            "max_drawdown": round(max_dd, 2),
            "avg_risk_reward": round(sum(rr_list) / len(rr_list), 2) if rr_list else None,
            "best_trade": round(max(pnl_list), 2) if pnl_list else None,
            "worst_trade": round(min(pnl_list), 2) if pnl_list else None,
        }


# Module-level singleton
trade_manager = TradeManager()
