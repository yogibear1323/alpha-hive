"""Turns a Risk-approved hive call into a broker order size, inside the hard limits.

risk $ = balance x min(requested %, MAX_RISK_PCT_PER_TRADE); units = risk $ / stop distance;
notional is capped at MAX_POSITION_USD, MAX_POSITION_PCT_OF_BALANCE and balance x the
account's max leverage (the terminal's virtual leverage is ignored: CFD margin is the
broker's). Lots are rounded DOWN to the lot step; an order below the minimum lot is refused,
never rounded up.
"""
from __future__ import annotations

import math

from bridge.guard import Blocked


def _f(v, default=None):
    try:
        v = float(v)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def plan(*, balance, side, entry, stop, target, risk_pct, limits, details: dict, entry_ref=None) -> dict:
    balance, entry, stop, target, risk_pct = map(_f, (balance, entry, stop, target, risk_pct))
    if not balance or balance <= 0:
        raise Blocked("no_balance", "Account balance is unavailable or zero: trading disabled.")
    if not entry or entry <= 0 or stop is None or target is None:
        raise Blocked("bad_levels", "Entry, stop and target are required.")
    if side == "buy" and not (stop < entry < target):
        raise Blocked("bad_levels", f"A buy needs stop < price < target (stop {stop}, price {entry}, target {target}).")
    if side == "sell" and not (target < entry < stop):
        raise Blocked("bad_levels", f"A sell needs target < price < stop (target {target}, price {entry}, stop {stop}).")
    dist = abs(entry - stop)
    ref = _f(entry_ref)
    if ref and abs(entry - ref) > limits.max_entry_drift * abs(ref - stop):
        raise Blocked("price_moved", f"Broker price {entry} is too far from the round's entry {ref} for its stop.")
    pct = min(risk_pct if risk_pct and risk_pct > 0 else limits.max_risk_pct_per_trade, limits.max_risk_pct_per_trade)
    risk_usd = balance * pct / 100
    units = risk_usd / dist
    lev = limits.account_max_leverage
    inst_lev = _f(details.get("leverage"))
    if inst_lev and inst_lev > 0:
        lev = min(lev, inst_lev)
    cap = min(limits.max_position_usd, balance * limits.max_position_pct_of_balance / 100, balance * lev)
    capped = units * entry > cap
    if capped:
        units = cap / entry
    lot_size = _f(details.get("lotSize"), 1.0) or 1.0
    step = _f(details.get("lotStep"), 0.01) or 0.01
    min_lot = _f(details.get("minLot"), step) or step
    max_lot = _f(details.get("maxLot"))
    lots = math.floor(units / lot_size / step + 1e-9) * step
    if max_lot and lots > max_lot:
        lots, capped = math.floor(max_lot / step + 1e-9) * step, True
    lots = round(lots, 8)
    if lots < min_lot or lots <= 0:
        raise Blocked("below_min_lot", f"Size {units / lot_size:.6f} lots is below the minimum {min_lot} lots for "
                                       "these limits; the bridge never rounds up.")
    units = lots * lot_size
    return {"side": side, "lots": lots, "units": round(units, 8), "notional_usd": round(units * entry, 2),
            "risk_pct": round(pct, 4), "risk_usd": round(units * dist, 2), "stop": stop, "target": target,
            "entry": entry, "stop_distance": dist, "leverage_cap": lev, "capped": capped,
            "margin_estimate_usd": round(units * entry / lev, 2)}
