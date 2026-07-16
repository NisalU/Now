"""Unit tests for the local analysis engine."""
import pytest
from trading.analysis.helpers import (
    ema, sma, rma, rsi, macd, vwap, atr, swing_points, cluster_levels,
    linear_regression, pct_change, true_range,
)
from trading.analysis.indicators import compute_all, compute_ema_levels
from trading.analysis.market_structure import detect_bos_choch
from trading.analysis.support_resistance import detect_sr_levels
from trading.analysis.volume import analyze_volume
from trading.analysis.fibonacci import compute_fibonacci


def _candles(n=50, start=100.0, drift=0.002, vol=1.0):
    """Generate synthetic candles."""
    import random
    random.seed(42)
    candles = []
    price = start
    for i in range(n):
        price *= 1 + random.gauss(drift, vol * 0.01)
        open_ = price * (1 + random.gauss(0, 0.002))
        high = max(open_, price) * (1 + abs(random.gauss(0, 0.003)))
        low = min(open_, price) * (1 - abs(random.gauss(0, 0.003)))
        volume = random.uniform(100, 1000)
        taker_buy = volume * random.uniform(0.3, 0.7)
        candles.append({
            "time": 1700000000 + i * 60,
            "open": open_,
            "high": high,
            "low": low,
            "close": price,
            "volume": volume,
            "delta": taker_buy * 2 - volume,
            "taker_buy_vol": taker_buy,
        })
    return candles


class TestHelpers:
    def test_ema_length(self):
        values = list(range(1, 21))
        result = ema(values, 5)
        assert len(result) == 20

    def test_ema_first_values_none(self):
        values = list(range(1, 21))
        result = ema(values, 5)
        assert all(v is None for v in result[:4])
        assert result[4] is not None

    def test_sma_basic(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = sma(values, 3)
        assert result[2] == pytest.approx(2.0)
        assert result[4] == pytest.approx(4.0)

    def test_rsi_range(self):
        closes = [100 + i * 0.5 for i in range(30)]
        r = rsi(closes)
        assert r is not None
        assert 0 <= r <= 100

    def test_rsi_overbought_trend(self):
        # Strongly trending up → RSI should be high
        closes = [100.0 * (1.01 ** i) for i in range(30)]
        r = rsi(closes)
        assert r is not None and r > 70

    def test_macd_keys(self):
        closes = [100 + i * 0.1 for i in range(50)]
        m = macd(closes)
        assert "macd" in m and "signal" in m and "histogram" in m

    def test_vwap_basic(self):
        candles = _candles(20)
        v = vwap(candles)
        assert v is not None
        assert v > 0

    def test_atr_positive(self):
        candles = _candles(20)
        assert atr(candles) > 0

    def test_swing_points(self):
        candles = _candles(40)
        highs, lows = swing_points(candles, lookback=3)
        assert isinstance(highs, list)
        assert isinstance(lows, list)

    def test_cluster_levels(self):
        points = [(0, 100.0), (1, 100.5), (2, 105.0), (3, 104.8)]
        clusters = cluster_levels(points, tolerance=1.0)
        assert len(clusters) == 2

    def test_linear_regression(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [0.0, 1.0, 2.0, 3.0]
        slope, intercept = linear_regression(xs, ys)
        assert slope == pytest.approx(1.0)
        assert intercept == pytest.approx(0.0)

    def test_pct_change(self):
        assert pct_change(100.0, 110.0) == pytest.approx(10.0)
        assert pct_change(100.0, 90.0) == pytest.approx(-10.0)


class TestIndicators:
    def test_compute_all_keys(self):
        candles = _candles(60)
        result = compute_all(candles)
        assert "price" in result
        assert "ema" in result
        assert "rsi" in result
        assert "macd" in result
        assert "vwap" in result
        assert "atr" in result

    def test_ema_levels(self):
        candles = _candles(220)
        result = compute_ema_levels(candles)
        assert result["ema20"] is not None
        assert result["ema50"] is not None
        assert result["ema200"] is not None


class TestMarketStructure:
    def test_detect_bos_returns_dict(self):
        candles = _candles(40)
        result = detect_bos_choch(candles)
        assert "trend" in result
        assert result["trend"] in ("bullish", "bearish", "neutral")
        assert "events" in result
        assert "swing_highs" in result
        assert "swing_lows" in result


class TestSupportResistance:
    def test_sr_keys(self):
        candles = _candles(80)
        result = detect_sr_levels(candles)
        assert "support" in result
        assert "resistance" in result


class TestVolume:
    def test_analyze_volume_keys(self):
        candles = _candles(50)
        result = analyze_volume(candles)
        assert "current_volume" in result
        assert "ratio" in result
        assert "buy_pct" in result
        assert "poc" in result

    def test_cvd_tail_length(self):
        candles = _candles(50)
        result = analyze_volume(candles)
        assert len(result["cvd_tail"]) <= 20


class TestFibonacci:
    def test_fibonacci_levels(self):
        candles = _candles(60)
        result = compute_fibonacci(candles)
        if result:  # may be None if no swings
            assert "retracement_levels" in result
            assert len(result["retracement_levels"]) == 5
            assert result["swing_high"] > result["swing_low"]
