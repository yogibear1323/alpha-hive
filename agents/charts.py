"""4 - Charts Bot: trend, levels, and volume from the numbers only."""
from agents.base import call_bot

NAME = "charts"
SCHEMA = {
    "trend": {"type": "str", "enum": ["up", "down", "sideways", "unknown"]},
    "support_levels": {"type": "list", "items": "number"},
    "resistance_levels": {"type": "list", "items": "number"},
    "volume_summary": {"type": "str"},
    "setup_quality": {"type": "str", "enum": ["good", "fair", "poor", "unknown"]},
    "missing_data": {"type": "list", "items": "str"},
}


def _numbers_only(packet: dict) -> dict:
    """Charts sees prices and volume, not headlines or posts."""
    keys = ("_label", "market", "as_of", "currency", "price", "volume",
            "avg_daily_dollar_volume_usd", "daily_bars", "intraday_bars")
    return {k: packet[k] for k in keys if k in packet}


def run(packet: dict, provider, logger=None) -> dict:
    out = call_bot(NAME, SCHEMA, {"market_packet": _numbers_only(packet)}, provider, logger)
    # No price in the packet means no chart read, whatever the model says.
    price = (packet.get("price") or {}).get("last") if isinstance(packet.get("price"), dict) else None
    if not isinstance(price, (int, float)) or price <= 0:
        out["trend"] = "unknown"
        out["setup_quality"] = "unknown"
        if "current price" not in out["missing_data"]:
            out["missing_data"].append("current price")
    return out
