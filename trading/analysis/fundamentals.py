"""Fundamental analysis: Funding Rate, Open Interest, Long/Short Ratio."""
from __future__ import annotations

from typing import Any


def analyze_fundamentals(
    futures_stats: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Interpret raw futures stats into trading-relevant signals.

    Returns None if futures data is unavailable.
    """
    if not futures_stats:
        return None

    funding_rate: float = futures_stats.get("funding_rate", 0.0)
    open_interest: float = futures_stats.get("open_interest", 0.0)
    long_short_ratio: float = futures_stats.get("long_short_ratio", 1.0)
    ls_change: float = futures_stats.get("ls_ratio_change", 0.0)

    # Funding interpretation
    funding_bias: str
    if funding_rate > 0.0005:
        funding_bias = "longs_crowded"
    elif funding_rate < -0.0005:
        funding_bias = "shorts_crowded"
    else:
        funding_bias = "balanced"

    # Long/short ratio interpretation
    ls_bias: str
    if long_short_ratio > 2.5:
        ls_bias = "extremely_long"
    elif long_short_ratio > 1.5:
        ls_bias = "longs_dominant"
    elif long_short_ratio < 0.5:
        ls_bias = "extremely_short"
    elif long_short_ratio < 0.7:
        ls_bias = "shorts_dominant"
    else:
        ls_bias = "balanced"

    # Funding rate as percentage (more readable)
    funding_pct = funding_rate * 100

    return {
        "funding_rate": funding_rate,
        "funding_pct": round(funding_pct, 4),
        "funding_bias": funding_bias,
        "open_interest": open_interest,
        "long_short_ratio": long_short_ratio,
        "ls_change": ls_change,
        "ls_bias": ls_bias,
        "contrarian_risk": funding_bias != "balanced" or "extreme" in ls_bias,
    }
