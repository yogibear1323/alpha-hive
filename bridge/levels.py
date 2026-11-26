"""Stop and target for a hive round, using the same rule as the terminal's live rounds:
stop distance = 1.5 x ATR (mean high-low of the last 14 one-minute candles; daily bars if
there are no intraday bars), at least 0.06% and at most 50% of the price; target = 2 x the
stop distance (2 : 1)."""
from __future__ import annotations


def atr(packet: dict, n: int = 14):
    bars = packet.get("intraday_bars") or packet.get("daily_bars") or []
    rng = [b["high"] - b["low"] for b in bars[-n:]
           if isinstance(b, dict) and isinstance(b.get("high"), (int, float)) and isinstance(b.get("low"), (int, float))]
    return sum(rng) / len(rng) if rng else None


def stop_target(entry: float, direction: int, atr_value: float) -> tuple[float, float, float]:
    if not (entry and entry > 0) or direction not in (1, -1) or not atr_value or atr_value <= 0:
        raise ValueError("need a positive entry, a direction of +1/-1 and a positive ATR")
    dist = min(max(1.5 * atr_value, entry * 0.0006), entry * 0.5)
    return entry - direction * dist, entry + direction * 2 * dist, dist
