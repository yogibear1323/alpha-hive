"""6 - Decision Bot: runs only after Risk approves or reduces.

Returns 'BUY — SIMULATION ONLY', 'WAIT', or 'SKIP'. human_review_required is forced
to true in code. A BUY is only allowed when Risk approved outright.
"""
from agents.base import DECISIONS, RiskRejectedError, call_bot

NAME = "decision"
SCHEMA = {
    "decision": {"type": "str", "enum": DECISIONS},
    "reasons": {"type": "list", "items": "str"},
    "conflicting_signals": {"type": "list", "items": "str"},
    "missing_information": {"type": "list", "items": "str"},
    "invalidation_conditions": {"type": "list", "items": "str"},
    "human_review_required": {"type": "bool"},
}


def run(packet: dict, research: dict, risk_result: dict, provider, logger=None) -> dict:
    status = (risk_result or {}).get("status")
    if status not in ("approve", "reduce"):
        raise RiskRejectedError(f"Decision Bot refused to run: Risk status is '{status}'.")
    payload = {"market": packet.get("market"), "research": research, "risk": risk_result}
    out = call_bot(NAME, SCHEMA, payload, provider, logger)
    out["human_review_required"] = True
    if out["decision"] == DECISIONS[0] and status != "approve":
        out["decision"] = "WAIT"
        out["reasons"] = out["reasons"] + ["Code downgraded BUY to WAIT: Risk did not fully approve."]
    return out
