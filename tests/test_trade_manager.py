"""Unit tests for the trade manager."""
import pytest
from trading.trade.manager import TradeManager

SIGNAL = {
    "symbol": "BTCUSDT",
    "signal": "LONG",
    "entry": 50000.0,
    "stop_loss": 49000.0,
    "take_profit_1": 52000.0,
    "take_profit_2": 53000.0,
    "confidence": 0.8,
    "risk_reward": 2.0,
    "setup_type": "OB",
}


class TestTradeManager:
    def setup_method(self):
        self.mgr = TradeManager()

    def test_open_trade(self):
        t = self.mgr.open_trade(SIGNAL)
        assert t.status == "active"
        assert t.direction == "LONG"
        assert t.entry == 50000.0

    def test_open_trade_appears_in_active(self):
        self.mgr.open_trade(SIGNAL)
        assert len(self.mgr.get_active()) == 1

    def test_sl_closes_trade(self):
        self.mgr.open_trade(SIGNAL)
        closed = self.mgr.update_price("BTCUSDT", 48900.0)  # below SL 49000
        assert len(closed) == 1
        assert closed[0].close_reason == "stop_loss"
        assert len(self.mgr.get_active()) == 0

    def test_tp1_triggers_partial(self):
        t = self.mgr.open_trade(SIGNAL)
        closed = self.mgr.update_price("BTCUSDT", 52100.0)  # above TP1 52000
        # Not fully closed because TP2 exists
        assert len(closed) == 0
        assert t.partial_tp_triggered is True

    def test_tp2_closes_after_partial(self):
        t = self.mgr.open_trade(SIGNAL)
        self.mgr.update_price("BTCUSDT", 52100.0)   # hit TP1 → partial
        closed = self.mgr.update_price("BTCUSDT", 53100.0)  # hit TP2
        assert len(closed) == 1
        assert closed[0].close_reason == "take_profit_2"

    def test_manual_close(self):
        t = self.mgr.open_trade(SIGNAL)
        result = self.mgr.close_trade(t.id, 51000.0)
        assert result is not None
        assert result.close_reason == "manual"
        assert result.realized_pnl == pytest.approx(2.0)

    def test_break_even_trigger(self):
        t = self.mgr.open_trade(SIGNAL)
        # Risk = 50000 - 49000 = 1000; 1:1 = 51000
        self.mgr.update_price("BTCUSDT", 51200.0)
        assert t.break_even_triggered is True
        assert t.stop_loss == pytest.approx(50000.0)

    def test_no_close_on_price_within_range(self):
        self.mgr.open_trade(SIGNAL)
        closed = self.mgr.update_price("BTCUSDT", 50500.0)
        assert closed == []

    def test_history_after_close(self):
        t = self.mgr.open_trade(SIGNAL)
        self.mgr.close_trade(t.id, 51000.0)
        history = self.mgr.get_history()
        assert len(history) == 1
        assert history[0]["id"] == t.id

    def test_performance_win_rate(self):
        for _ in range(3):
            t = self.mgr.open_trade(SIGNAL)
            self.mgr.close_trade(t.id, 52000.0)  # win
        t = self.mgr.open_trade(SIGNAL)
        self.mgr.close_trade(t.id, 49000.0)  # loss
        perf = self.mgr.get_performance()
        assert perf["total_trades"] == 4
        assert perf["winning_trades"] == 3
        assert perf["win_rate"] == pytest.approx(75.0)

    def test_short_sl_closes_correctly(self):
        short_signal = {
            **SIGNAL,
            "signal": "SHORT",
            "entry": 50000.0,
            "stop_loss": 51000.0,
            "take_profit_1": 48000.0,
            "take_profit_2": 47000.0,
        }
        self.mgr.open_trade(short_signal)
        closed = self.mgr.update_price("BTCUSDT", 51100.0)  # above SHORT SL
        assert len(closed) == 1
        assert closed[0].close_reason == "stop_loss"
