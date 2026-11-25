"""2 - News Bot: separates reporting from unverified claims."""
from agents.base import call_bot

NAME = "news"
SCHEMA = {
    "summary": {"type": "str"},
    "positive_events": {"type": "list", "items": "str"},
    "negative_events": {"type": "list", "items": "str"},
    "unverified_claims": {"type": "list", "items": "str"},
    "sources": {"type": "list", "items": "str"},
    "last_updated": {"type": "str"},
}


def run(packet: dict, provider, logger=None) -> dict:
    return call_bot(NAME, SCHEMA, {"market_packet": packet}, provider, logger)
