"""Unit tests for the risk validator."""
import pytest
from trading.risk.validator import RiskValidator

validator = RiskValidator()

BASE_SIGNAL = {
    "symbol": "BTCUSDT",
    "signal": "LONG",
    "entry": 50000.0,
    "stop_loss": 49000.0,
    "take_profit_1": 52000.0,
    "take_profit_2": 53000.0,
    "confidence": 0.8,
    "htf_bias": "LONG",
    "setup_type": "OB reclaim",
    "reasoning": "Test",
    "invalidation": "Below SL",
}

PRICE = 50100.0
ATR = 500.0


def _clone(**kwargs):
    s = dict(BASE_SIGNAL)
    s.update(kwargs)
    return s


class TestValidator:
    def test_valid_long_passes(self):
        s = validator.validate(_clone(), PRICE, ATR)
        assert s["gated"] is False
        assert s["signal"] == "LONG"

    def test_valid_short_passes(self):
        s = _clone(
            signal="SHORT",
            entry=50000.0,
            stop_loss=51000.0,
            take_profit_1=48000.0,
        )
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is False

    def test_wait_always_passes(self):
        s = {
            "signal": "WAIT",
            "entry": None,
            "stop_loss": None,
            "take_profit_1": None,
        }
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is False

    def test_missing_entry_gates(self):
        s = _clone(entry=None)
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is True
        assert result["signal"] == "WAIT"

    def test_missing_sl_gates(self):
        s = _clone(stop_loss=None)
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is True

    def test_missing_tp_gates(self):
        s = _clone(take_profit_1=None)
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is True

    def test_sl_above_entry_long_gates(self):
        s = _clone(stop_loss=51000.0)  # SL above entry for LONG
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is True

    def test_tp_below_entry_long_gates(self):
        s = _clone(take_profit_1=49000.0)  # TP below entry for LONG
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is True

    def test_low_rr_gates(self):
        # RR = (50500 - 50000) / (50000 - 49000) = 0.5 — below 2.0
        s = _clone(take_profit_1=50500.0)
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is True

    def test_rr_calculated_on_pass(self):
        s = validator.validate(_clone(), PRICE, ATR)
        # RR = (52000-50000)/(50000-49000) = 2.0
        assert s.get("risk_reward") == pytest.approx(2.0)

    def test_entry_too_far_gates(self):
        # Entry is 5 ATR away from price (2500 from 50100) — above max distance
        s = _clone(
            entry=47500.0,
            stop_loss=47000.0,
            take_profit_1=48500.0,
        )
        result = validator.validate(s, PRICE, ATR)
        assert result["gated"] is True

    def test_gate_reason_populated(self):
        s = _clone(entry=None)
        result = validator.validate(s, PRICE, ATR)
        assert isinstance(result["gate_reason"], str)
        assert len(result["gate_reason"]) > 0
