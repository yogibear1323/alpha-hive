"""3 - Sentiment Bot: crowd mood and hype detection."""
from agents.base import call_bot

NAME = "sentiment"
SCHEMA = {
    "sentiment": {"type": "str", "enum": ["positive", "negative", "mixed", "neutral", "unknown"]},
    "score": {"type": "int", "min": -100, "max": 100},
    "main_reasons": {"type": "list", "items": "str"},
    "possible_hype": {"type": "list", "items": "str"},
    "sources": {"type": "list", "items": "str"},
}


def run(packet: dict, provider, logger=None) -> dict:
    return call_bot(NAME, SCHEMA, {"market_packet": packet}, provider, logger)
