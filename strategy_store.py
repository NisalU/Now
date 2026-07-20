"""Persistent store for user-created custom strategies.

Strategies are saved to strategy_store.json alongside server.py.
Thread-safe: all mutations go through _lock.
"""
from __future__ import annotations
import json
import os
import threading
import time
import uuid

_STORE_FILE = os.path.join(os.path.dirname(__file__), "strategy_store.json")
_lock       = threading.Lock()

# ── in-memory state ───────────────────────────────────────────────────────────
_strategies: dict[str, dict] = {}   # id -> strategy dict


def _load() -> None:
    global _strategies
    try:
        with open(_STORE_FILE) as fh:
            data = json.load(fh)
            _strategies = {s["id"]: s for s in data.get("strategies", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        _strategies = {}


def _save() -> None:
    try:
        with open(_STORE_FILE, "w") as fh:
            json.dump({"strategies": list(_strategies.values())}, fh, indent=2)
    except Exception as exc:  # noqa: BLE001
        print(f"[strategy_store] save error: {exc}")


# load on import
_load()


# ── public API ────────────────────────────────────────────────────────────────

def list_strategies() -> list[dict]:
    with _lock:
        return list(_strategies.values())


def get_strategy(strategy_id: str) -> dict | None:
    with _lock:
        return _strategies.get(strategy_id)


def create_strategy(data: dict) -> dict:
    """Create a new strategy. Returns the saved strategy dict with id."""
    strategy = {
        "id":               str(uuid.uuid4()),
        "name":             str(data.get("name", "Untitled")).strip()[:80],
        "description":      str(data.get("description", "")).strip()[:300],
        "weight":           max(0, min(20, int(data.get("weight", 8)))),
        "enabled":          bool(data.get("enabled", True)),
        "signal_direction": data.get("signal_direction", "bullish"),
        "logic":            data.get("logic", "AND").upper(),
        "conditions":       _validate_conditions(data.get("conditions", [])),
        "created_at":       int(time.time()),
        "updated_at":       int(time.time()),
    }
    with _lock:
        _strategies[strategy["id"]] = strategy
        _save()
    return strategy


def update_strategy(strategy_id: str, data: dict) -> dict | None:
    """Partial update. Returns updated strategy or None if not found."""
    with _lock:
        existing = _strategies.get(strategy_id)
        if existing is None:
            return None
        if "name" in data:
            existing["name"]             = str(data["name"]).strip()[:80]
        if "description" in data:
            existing["description"]      = str(data["description"]).strip()[:300]
        if "weight" in data:
            existing["weight"]           = max(0, min(20, int(data["weight"])))
        if "enabled" in data:
            existing["enabled"]          = bool(data["enabled"])
        if "signal_direction" in data:
            existing["signal_direction"] = data["signal_direction"]
        if "logic" in data:
            existing["logic"]            = str(data["logic"]).upper()
        if "conditions" in data:
            existing["conditions"]       = _validate_conditions(data["conditions"])
        existing["updated_at"] = int(time.time())
        _save()
        return existing


def delete_strategy(strategy_id: str) -> bool:
    with _lock:
        if strategy_id not in _strategies:
            return False
        del _strategies[strategy_id]
        _save()
        return True


# ── validation ────────────────────────────────────────────────────────────────

VALID_CONDITION_TYPES = {
    "price_above_ema", "price_below_ema",
    "ema_cross_above", "ema_cross_below",
    "rsi_above", "rsi_below",
    "volume_spike",
    "candle_bullish", "candle_bearish",
    "delta_positive", "delta_negative",
    "price_change_above", "price_change_below",
    "atr_expansion",
    "cvd_rising", "cvd_falling",
    "near_support", "near_resistance",
}


def _validate_conditions(conditions: list) -> list[dict]:
    valid = []
    for c in conditions[:20]:    # max 20 conditions
        if not isinstance(c, dict):
            continue
        ct = str(c.get("type", "")).strip()
        if ct not in VALID_CONDITION_TYPES:
            continue
        valid.append({
            "type":   ct,
            "label":  str(c.get("label", ct)).strip()[:60],
            "params": {k: v for k, v in (c.get("params") or {}).items()
                       if isinstance(k, str) and isinstance(v, (int, float, str))},
        })
    return valid
