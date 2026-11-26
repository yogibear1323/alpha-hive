"""Deterministic offline stand-ins for the six Grok bots.

They read the same payload a live model would see and compute plain, rule-based
answers, so the demo and tests work with no key and no network. On the EXAMPLE
packet they reproduce the guide's example run (Risk reduce, size halved, WAIT).
Instruction-like text inside the data is recorded as an unverified claim and is
never obeyed - exactly what we expect a live model to do.
"""
from __future__ import annotations

import re

INJECTION_RE = re.compile(
    r"(ignore|disregard|override|forget)\b.{0,40}\b(rules?|instructions?|limits?|prompt)"
    r"|\b(as an ai|note to any ai|system prompt)\b",
    re.I,
)
HYPE_RE = re.compile(r"(to the moon|🚀|guaranteed|\b\d{3,}%|load up|can't lose|100x)", re.I)


def _packet(payload):
    return payload.get("market_packet", payload) or {}


def _price(p):
    price = p.get("price") or {}
    last = price.get("last") if isinstance(price, dict) else None
    return last if isinstance(last, (int, float)) and last > 0 else None


def _sources(p):
    out = []
    for h in p.get("headlines") or []:
        if h.get("source") and h["source"] not in out:
            out.append(h["source"])
    for post in p.get("forum_posts") or []:
        if post.get("source") and post["source"] not in out:
            out.append(post["source"])
    return out or ["provided market packet"]


# ------------------------------------------------------------------ 1 Scout
def scout(payload):
    p = _packet(payload)
    market = str(p.get("market") or "UNKNOWN")
    last = _price(p)
    vol = p.get("volume") or {}
    today, avg = vol.get("today"), vol.get("avg_30d")
    change = (p.get("price") or {}).get("change_pct")
    dollar_vol = p.get("avg_daily_dollar_volume_usd")

    if last is None:
        return {
            "market": market,
            "reason_to_research": "No usable price data in the packet; cannot assess this market.",
            "liquidity_status": "unknown",
            "data_quality": "poor",
            "sources": _sources(p),
            "confidence": 10,
        }

    parts = []
    if today and avg:
        parts.append(f"Volume {today / avg:.1f}x the 30-day average")
    if isinstance(change, (int, float)):
        parts.append(f"after a {change:.1f}% move")
    reason = " ".join(parts) or "Price data present; no unusual activity flagged."

    if isinstance(dollar_vol, (int, float)):
        liquidity = "adequate" if dollar_vol >= 1_000_000 else "thin"
    else:
        liquidity = "unknown"
    complete = bool(p.get("daily_bars")) and bool(p.get("intraday_bars"))
    quality = "good" if complete else "partial"
    confidence = 65 if quality == "partial" else 80
    if liquidity != "adequate":
        confidence -= 20
    return {
        "market": market,
        "reason_to_research": reason,
        "liquidity_status": liquidity,
        "data_quality": quality,
        "sources": _sources(p),
        "confidence": confidence,
    }


# ------------------------------------------------------------------ 2 News
def news(payload):
    p = _packet(payload)
    positive, negative = [], []
    for h in p.get("headlines") or []:
        title = h.get("title", "")
        if h.get("kind", "reported") != "reported":
            continue
        (negative if h.get("tone") == "negative" else positive).append(
            f"{title} ({h.get('source', 'unknown source')}, reported)"
        )
    unverified = []
    for post in p.get("forum_posts") or []:
        text = post.get("text", "")
        if INJECTION_RE.search(text):
            unverified.append(
                f"Forum post (unverified, treated as data, not an instruction): \"{text}\""
            )
    summary = (
        f"{len(positive)} reported positive item(s), {len(negative)} reported negative item(s), "
        f"{len(unverified)} unverified claim(s) from forums."
    )
    dates = [h.get("date") for h in (p.get("headlines") or []) if h.get("date")]
    dates += [x.get("date") for x in (p.get("forum_posts") or []) if x.get("date")]
    return {
        "summary": summary,
        "positive_events": positive,
        "negative_events": negative,
        "unverified_claims": unverified,
        "sources": _sources(p),
        "last_updated": max(dates) if dates else str(p.get("as_of") or "unknown"),
    }


# ------------------------------------------------------------------ 3 Sentiment
def sentiment(payload):
    p = _packet(payload)
    posts = p.get("forum_posts") or []
    heads = [h for h in (p.get("headlines") or []) if h.get("kind", "reported") == "reported"]
    hype = [x.get("text", "") for x in posts if HYPE_RE.search(x.get("text", ""))]
    pos = sum(1 for h in heads if h.get("tone") == "positive")
    neg = sum(1 for h in heads if h.get("tone") == "negative")
    score = max(-100, min(100, 50 * pos - 50 * neg - 25 * len(hype)))
    if not posts and not heads:
        label = "unknown"
    elif hype and score > 0:
        label = "mixed"
    elif score > 0:
        label = "positive"
    elif score < 0:
        label = "negative"
    else:
        label = "neutral"
    reasons = []
    if pos:
        reasons.append(f"{pos} positive reported headline(s)")
    if neg:
        reasons.append(f"{neg} negative reported headline(s)")
    if hype:
        reasons.append(f"{len(hype)} hype-style post(s) discounted")
    cautious = [x for x in posts if re.search(r"\bwait(ing)?\b", x.get("text", ""), re.I)]
    if cautious:
        reasons.append(f"{len(cautious)} cautious post(s)")
    return {
        "sentiment": label,
        "score": score,
        "main_reasons": reasons or ["not enough posts or headlines"],
        "possible_hype": hype,
        "sources": _sources(p),
    }


# ------------------------------------------------------------------ 4 Charts
def charts(payload):
    p = _packet(payload)
    bars = [b for b in (p.get("daily_bars") or []) if isinstance(b, dict)]
    last = _price(p)
    missing = []
    if last is None:
        missing.append("current price")
    if not bars:
        missing.append("daily bars")
    if not p.get("intraday_bars"):
        missing.append("intraday data")
    if last is None or len(bars) < 2:
        return {
            "trend": "unknown",
            "support_levels": [],
            "resistance_levels": [],
            "volume_summary": "Not enough numeric data to read volume.",
            "setup_quality": "unknown",
            "missing_data": missing,
        }
    first_close, last_close = bars[0]["close"], bars[-1]["close"]
    change = (last_close - first_close) / first_close * 100
    trend = "up" if change > 1 else "down" if change < -1 else "sideways"
    support = round(min(b["low"] for b in bars), 2)
    resistance = round(max(b["high"] for b in bars), 2)
    vol = p.get("volume") or {}
    if vol.get("today") and vol.get("avg_30d"):
        vs = f"Latest volume {vol['today'] / vol['avg_30d']:.1f}x the 30-day average."
    else:
        vs = "Volume comparison unavailable."
    quality = "fair" if missing else "good"
    return {
        "trend": trend,
        "support_levels": [support],
        "resistance_levels": [resistance],
        "volume_summary": vs,
        "setup_quality": quality,
        "missing_data": missing,
    }


# ------------------------------------------------------------------ 5 Risk
def risk(payload):
    r = payload.get("research", {})
    rules = payload.get("risk_rules", {})
    bal = float(payload.get("simulation_balance_usd", 0))
    flags = []
    s, n, se, c = r.get("scout", {}), r.get("news", {}), r.get("sentiment", {}), r.get("charts", {})
    if payload.get("price_available") is False:
        flags.append("no_price_data")
    if s.get("data_quality") == "poor":
        flags.append("data_quality_poor")
    if c.get("trend") == "unknown":
        flags.append("chart_data_unknown")
    if s.get("liquidity_status") == "thin":
        flags.append("liquidity_below_minimum")
    if n.get("unverified_claims"):
        flags.append("unverified_claims")
    if se.get("possible_hype"):
        flags.append("possible_hype")
    if c.get("missing_data"):
        flags.append("incomplete_chart_data")

    size = bal * rules.get("max_position_pct_of_balance", 0) / 100
    loss = bal * rules.get("max_loss_pct_per_trade", 0) / 100
    if any(f in rules.get("reject_if_any", []) for f in flags):
        return {
            "status": "reject",
            "maximum_simulated_size": 0,
            "maximum_simulated_loss": 0,
            "risk_flags": flags,
            "reason": "A reject rule was triggered: "
            + ", ".join(f for f in flags if f in rules.get("reject_if_any", [])),
        }
    if flags:
        return {
            "status": "reduce",
            "maximum_simulated_size": round(size / 2, 2),
            "maximum_simulated_loss": round(loss / 2, 2),
            "risk_flags": flags,
            "reason": "Reduced: " + ", ".join(flags).replace("_", " ")
            + ". Simulated size halved.",
        }
    return {
        "status": "approve",
        "maximum_simulated_size": round(size, 2),
        "maximum_simulated_loss": round(loss, 2),
        "risk_flags": [],
        "reason": "Within all human-written limits.",
    }


# ------------------------------------------------------------------ 6 Decision
def decision(payload):
    r = payload.get("research", {})
    risk_out = payload.get("risk", {})
    c, se, n, s = r.get("charts", {}), r.get("sentiment", {}), r.get("news", {}), r.get("scout", {})
    conflicts, missing, reasons = [], [], []
    if c.get("trend") == "up" and se.get("possible_hype"):
        conflicts.append("Uptrend and volume support the move, but part of the crowd mood is hype.")
    if c.get("trend") == "down" and se.get("sentiment") == "positive":
        conflicts.append("Downtrend on the chart, but reported news and crowd mood are positive.")
    if c.get("trend") == "down" and len(n.get("positive_events", [])) > len(n.get("negative_events", [])):
        conflicts.append("Downtrend on the chart, but reported news leans positive.")
    if n.get("positive_events") and n.get("unverified_claims"):
        conflicts.append("A reported revenue item sits next to an unverified forum claim.")
    missing += c.get("missing_data", [])
    if s.get("data_quality") != "good":
        missing.append(f"scout data quality is {s.get('data_quality')}")
    reasons.append(f"Risk status is '{risk_out.get('status')}': {risk_out.get('reason', '')}")
    if c.get("trend"):
        reasons.append(f"Chart trend {c['trend']}; setup quality {c.get('setup_quality')}.")
    clean = risk_out.get("status") == "approve" and not conflicts and not missing
    bearish = se.get("sentiment") in ("negative", "neutral", "unknown", "mixed") and se.get("score", 0) <= 0
    if clean and c.get("trend") == "up":
        verdict = "BUY — SIMULATION ONLY"
    elif clean and c.get("trend") == "down" and bearish:
        verdict = "SELL/SHORT — SIMULATION ONLY"
    elif c.get("trend") in ("down", "unknown"):
        verdict = "SKIP"
    else:
        verdict = "WAIT"
    inval = []
    if c.get("support_levels"):
        inval.append(f"Daily close below support {c['support_levels'][0]}")
    if c.get("resistance_levels"):
        inval.append(f"Rejection at resistance {c['resistance_levels'][0]} on falling volume")
    inval.append("Unverified claims turn out false or the revenue report is revised")
    return {
        "decision": verdict,
        "reasons": reasons,
        "conflicting_signals": conflicts,
        "missing_information": missing,
        "invalidation_conditions": inval,
        "human_review_required": True,
    }


_BOTS = {
    "scout": scout,
    "news": news,
    "sentiment": sentiment,
    "charts": charts,
    "risk": risk,
    "decision": decision,
}


def respond(bot: str, payload: dict) -> dict:
    return _BOTS[bot](payload)
