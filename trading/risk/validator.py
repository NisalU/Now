"""Risk Validator.

Validates every non-WAIT AI signal before it is published or acted upon.
Checks: SL/TP presence, RR >= 2.0, entry not chasing price, no missing fields.

Rejection reasons are attached to the signal as gate_reason.
Validated signals pass unchanged (gated=False).
"""
from __future__ import annotations

import logging
from typing import Any

from trading import config

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when a signal fails a hard validation check."""


class RiskValidator:
    """Stateless signal validator."""

    def validate(
        self,
        signal: dict[str, Any],
        current_price: float,
        atr_val: float,
    ) -> dict[str, Any]:
        """Validate signal and return it annotated with gated/gate_reason.

        For WAIT signals: always pass (nothing to validate).
        For LONG/SHORT: run all checks; gate if any fail.
        """
        if signal.get("signal") == "WAIT":
            signal["gated"] = False
            signal["gate_reason"] = None
            return signal

        try:
            self._check_required_fields(signal)
            self._check_entry_distance(signal, current_price, atr_val)
            self._check_stop_loss(signal, current_price)
            self._check_take_profit(signal, current_price)
            self._check_risk_reward(signal)
            signal["risk_reward"] = self._calc_rr(signal)
            signal["gated"] = False
            signal["gate_reason"] = None
            logger.info(
                "Signal validated [%s] %s entry=%.4f SL=%.4f TP1=%.4f RR=%.2f",
                signal.get("symbol"),
                signal.get("signal"),
                signal.get("entry", 0),
                signal.get("stop_loss", 0),
                signal.get("take_profit_1", 0),
                signal.get("risk_reward", 0),
            )
        except ValidationError as exc:
            logger.warning(
                "Signal gated [%s] %s: %s",
                signal.get("symbol"),
                signal.get("signal"),
                exc,
            )
            signal["signal"] = "WAIT"
            signal["gated"] = True
            signal["gate_reason"] = str(exc)

        return signal

    # ── Private checks ────────────────────────────────────────────────────────

    @staticmethod
    def _check_required_fields(signal: dict[str, Any]) -> None:
        for field in ("entry", "stop_loss", "take_profit_1"):
            if signal.get(field) is None:
                raise ValidationError(f"Missing field: {field}")

    @staticmethod
    def _check_entry_distance(
        signal: dict[str, Any], current_price: float, atr_val: float
    ) -> None:
        """Reject entries that are chasing the market too far from current price."""
        if atr_val <= 0:
            return
        entry = float(signal["entry"])
        dist_atr = abs(entry - current_price) / atr_val
        if dist_atr > config.MAX_ENTRY_ATR_DISTANCE:
            raise ValidationError(
                f"Entry {entry:.4f} is {dist_atr:.1f} ATR from price {current_price:.4f} "
                f"(max {config.MAX_ENTRY_ATR_DISTANCE} ATR) — chasing the market"
            )

    @staticmethod
    def _check_stop_loss(signal: dict[str, Any], current_price: float) -> None:
        """Stop loss must be on the correct side of current price."""
        sig_dir = signal["signal"]
        sl = float(signal["stop_loss"])
        entry = float(signal["entry"])

        if sig_dir == "LONG":
            if sl >= entry:
                raise ValidationError(
                    f"LONG stop loss {sl:.4f} must be below entry {entry:.4f}"
                )
        elif sig_dir == "SHORT":
            if sl <= entry:
                raise ValidationError(
                    f"SHORT stop loss {sl:.4f} must be above entry {entry:.4f}"
                )

    @staticmethod
    def _check_take_profit(signal: dict[str, Any], current_price: float) -> None:
        """Take profit must be on the correct side of entry."""
        sig_dir = signal["signal"]
        tp1 = float(signal["take_profit_1"])
        entry = float(signal["entry"])

        if sig_dir == "LONG" and tp1 <= entry:
            raise ValidationError(f"LONG TP1 {tp1:.4f} must be above entry {entry:.4f}")
        if sig_dir == "SHORT" and tp1 >= entry:
            raise ValidationError(f"SHORT TP1 {tp1:.4f} must be below entry {entry:.4f}")

    @staticmethod
    def _calc_rr(signal: dict[str, Any]) -> float:
        entry = float(signal["entry"])
        sl = float(signal["stop_loss"])
        tp1 = float(signal["take_profit_1"])
        risk = abs(entry - sl)
        if risk == 0:
            return 0.0
        reward = abs(tp1 - entry)
        return round(reward / risk, 2)

    def _check_risk_reward(self, signal: dict[str, Any]) -> None:
        rr = self._calc_rr(signal)
        if rr < config.MIN_RISK_REWARD:
            raise ValidationError(
                f"Risk:Reward {rr:.2f} is below minimum {config.MIN_RISK_REWARD} — poor setup"
            )


# Module-level singleton
risk_validator = RiskValidator()
