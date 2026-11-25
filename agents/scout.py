"""1 - Scout Bot: is this market worth researching? Liquidity and data quality."""
from agents.base import call_bot

NAME = "scout"
SCHEMA = {
    "market": {"type": "str"},
    "reason_to_research": {"type": "str"},
    "liquidity_status": {"type": "str", "enum": ["adequate", "thin", "unknown"]},
    "data_quality": {"type": "str", "enum": ["good", "partial", "poor"]},
    "sources": {"type": "list", "items": "str"},
    "confidence": {"type": "int", "min": 0, "max": 100},
}


def run(packet: dict, provider, logger=None) -> dict:
    return call_bot(NAME, SCHEMA, {"market_packet": packet}, provider, logger)
