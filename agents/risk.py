"""5 - Risk Bot: the gate.

The model reads the human-written rules and proposes a status and size. Then this
code enforces the rules itself: it may only make the answer stricter. Even if a
model returns a huge size, the size is capped here.
"""
from agents.base import call_bot

NAME = "risk"
SCHEMA = {
    "status": {"type": "str", "enum": ["approve", "reduce", "reject"]},
    "maximum_simulated_size": {"type": "number", "min": 0},
    "maximum_simulated_loss": {"type": "number", "min": 0},
    "risk_flags": {"type": "list", "items": "str"},
    "reason": {"type": "str"},
}

SEVERITY = {"approve": 0, "reduce": 1, "reject": 2}


def code_flags(packet: dict, research: dict, rules: dict) -> list[str]:
    """Flags computed in plain Python from the packet and the four research outputs."""
    flags = []
    price = packet.get("price") if isinstance(packet.get("price"), dict) else {}
    last = price.get("last")
    if not isinstance(last, (int, float)) or last <= 0:
        flags.append("no_price_data")
    liq = packet.get("avg_daily_dollar_volume_usd")
    min_liq = rules.get("min_liquidity_usd", 0)
    if not isinstance(liq, (int, float)) or liq < min_liq:
        flags.append("liquidity_below_minimum")
    if research.get("scout", {}).get("data_quality") == "poor":
        flags.append("data_quality_poor")
    charts = research.get("charts", {})
    if charts.get("trend") == "unknown":
        flags.append("chart_data_unknown")
    if research.get("news", {}).get("unverified_claims"):
        flags.append("unverified_claims")
    if research.get("sentiment", {}).get("possible_hype"):
        flags.append("possible_hype")
    if charts.get("missing_data"):
        flags.append("incomplete_chart_data")
    return flags


def limits(rules: dict, balance: float) -> tuple[float, float]:
    """Hard caps from the human rules, in simulated dollars."""
    size_cap = balance * float(rules.get("max_position_pct_of_balance", 0)) / 100
    loss_pct = min(float(rules.get("max_loss_pct_per_trade", 0)),
                   float(rules.get("max_daily_loss_pct", rules.get("max_loss_pct_per_trade", 0))))
    loss_cap = balance * loss_pct / 100
    return size_cap, loss_cap


def enforce(model_out: dict, packet: dict, research: dict, rules: dict, balance: float) -> dict:
    """Apply the human rules on top of the model's answer. Tighten only, never loosen."""
    flags = code_flags(packet, research, rules)
    reject_rules = set(rules.get("reject_if_any", []))
    reduce_rules = set(rules.get("reduce_if_any", []))

    code_status = "approve"
    if any(f in reject_rules for f in flags):
        code_status = "reject"
    elif any(f in reduce_rules for f in flags):
        code_status = "reduce"

    model_status = model_out.get("status", "reject")
    status = max(model_status, code_status, key=lambda s: SEVERITY.get(s, 2))

    size_cap, loss_cap = limits(rules, balance)
    if status == "reduce":
        size_cap, loss_cap = size_cap / 2, loss_cap / 2
    elif status == "reject":
        size_cap, loss_cap = 0.0, 0.0

    def cap(value, ceiling):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        if value != value or value < 0:  # NaN or negative
            value = 0.0
        return round(min(value, ceiling), 2)

    out = dict(model_out)
    out["status"] = status
    out["maximum_simulated_size"] = cap(model_out.get("maximum_simulated_size"), size_cap)
    out["maximum_simulated_loss"] = cap(model_out.get("maximum_simulated_loss"), loss_cap)
    merged = list(model_out.get("risk_flags", []))
    for f in flags:
        if f not in merged:
            merged.append(f)
    out["risk_flags"] = merged
    if status != model_status:
        out["reason"] = (f"{model_out.get('reason', '')} [Code enforced '{status}' "
                         f"from human rules; flags: {', '.join(flags)}]").strip()
    return out


def run(packet: dict, research: dict, provider, rules: dict, config: dict, logger=None) -> dict:
    balance = float(config.get("simulation", {}).get("starting_balance_usd", 0))
    price = packet.get("price") if isinstance(packet.get("price"), dict) else {}
    payload = {
        "market": packet.get("market"),
        "price_available": isinstance(price.get("last"), (int, float)) and price.get("last", 0) > 0,
        "avg_daily_dollar_volume_usd": packet.get("avg_daily_dollar_volume_usd"),
        "research": research,
        "risk_rules": {k: v for k, v in rules.items() if not k.startswith("_")},
        "simulation_balance_usd": balance,
    }
    model_out = call_bot(NAME, SCHEMA, payload, provider, logger)
    final = enforce(model_out, packet, research, rules, balance)
    if logger is not None and final != model_out:
        logger.log(f"risk: code enforcement changed model output -> status={final['status']}, "
                   f"size={final['maximum_simulated_size']}, loss={final['maximum_simulated_loss']}")
    return final
