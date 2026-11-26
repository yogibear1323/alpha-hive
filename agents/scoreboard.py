"""Accuracy scoreboard for hive rounds (simulation-side logging only).

Each round the Queen holds is appended to reports/scoreboard.jsonl: time, token, the
Queen's call, each bot's view (Scout, News, Sentiment, Charts), the Risk verdict and the
start price. When the trade window closes, record_outcome() adds the exit, the outcome
(target hit, stop hit, timed out) and the P&L. It also grades the "would-have"
direction: did price move the way each bot leaned? That gives every bot a directional hit
rate even on WAIT/SKIP rounds. The lean rules match the terminal (web/templates/index.html).
This module only reads reports and writes a local log. It cannot trade.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

FLAT = 0.0005          # moves under 0.05% count as flat and are not scored
MIN_SAMPLE = 30        # below this the scoreboard warns that the sample is small
WORKERS = ("news", "sentiment", "charts")
CALLS = {"BUY": "LONG", "SELL": "SHORT", "WAIT": "WAIT", "SKIP": "SKIP"}


def call_key(final_result: str | None) -> str:
    """'BUY — SIMULATION ONLY' -> LONG, 'SELL/SHORT …' -> SHORT, else WAIT/SKIP."""
    s = (final_result or "SKIP").upper()
    for k, v in CALLS.items():
        if s.startswith(k):
            return v
    return "SKIP"


def lean(bot: str, out: dict | None) -> str:
    """bull / bear / neu for a worker's output, the same rule the terminal uses."""
    if not isinstance(out, dict):
        return "neu"
    if bot == "news":
        p, n = len(out.get("positive_events") or []), len(out.get("negative_events") or [])
        return "bull" if p > n else "bear" if n > p else "neu"
    if bot == "sentiment":
        score = out.get("score") if isinstance(out.get("score"), (int, float)) else 0
        if out.get("sentiment") == "positive" or score >= 20:
            return "bull"
        if out.get("sentiment") == "negative" or score <= -20:
            return "bear"
        return "neu"
    if bot == "charts":
        return {"up": "bull", "down": "bear"}.get(out.get("trend"), "neu")
    return "neu"


def move_of(p0, p1) -> int | None:
    """+1 up, -1 down, 0 flat (|move| < FLAT), None if a price is missing."""
    try:
        p0, p1 = float(p0), float(p1)
    except (TypeError, ValueError):
        return None
    if p0 <= 0 or p1 <= 0:
        return None
    m = (p1 - p0) / p0
    return 0 if abs(m) < FLAT else (1 if m > 0 else -1)


def _dir(v: str) -> int:
    return 1 if v in ("bull", "LONG") else -1 if v in ("bear", "SHORT") else 0


def grade(rec: dict) -> dict:
    """Fill rec['hits'] (1 hit / 0 miss / None unscored) for each worker and the Queen."""
    mv = rec.get("move")
    hits = {}
    for b in (*WORKERS, "queen"):
        d = _dir(rec["call"] if b == "queen" else rec["views"].get(b, "neu"))
        hits[b] = None if (not mv or not d) else int(d == mv)
    rec["hits"] = hits
    return rec


def round_record(report: dict, packet: dict | None = None, venue: str = "sim") -> dict:
    out = report.get("outputs") or {}
    price = ((packet or {}).get("price") or {}).get("last")
    scout = out.get("scout")
    rec = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": report.get("run_id"),
        "token": report.get("market"),
        "venue": venue,
        "call": call_key(report.get("final_result")),
        "views": {"scout": f"screener · data {scout.get('data_quality', '?')}" if isinstance(scout, dict) else "—",
                  **{b: lean(b, out.get(b)) if b in out else "—" for b in WORKERS}},
        "risk": (out.get("risk") or {}).get("status") or report.get("stopped_at") or "—",
        "stopped_at": report.get("stopped_at"),
        "price_start": price, "entry": None, "stop": None, "target": None, "exit": None,
        "price_end": None, "outcome": "pending", "pnl": 0.0, "risk_usd": None, "R": None, "move": None,
    }
    return grade(rec)


def log_round(report: dict, packet: dict | None, path: Path, venue: str = "sim") -> dict:
    rec = round_record(report, packet, venue)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def record_outcome(path: Path, run_id: str, price_end: float, *, outcome: str = "timed out",
                   entry=None, stop=None, target=None, exit=None, pnl: float = 0.0,
                   risk_usd: float | None = None) -> dict | None:
    """Close a logged round when its trade window ends. For a trade pass entry/stop/target/
    exit/pnl/risk_usd and outcome ('target hit', 'stop hit', 'timed out'); for WAIT/SKIP pass
    only price_end (the would-have result). Returns the updated record, or None if not found."""
    rows = load(path)
    hit = None
    for r in rows:
        if r.get("run_id") == run_id:
            hit = r
    if hit is None:
        return None
    traded = entry is not None and exit is not None
    hit.update({"entry": entry, "stop": stop, "target": target, "exit": exit, "price_end": price_end,
                "outcome": outcome if traded else "no trade", "pnl": round(float(pnl), 2) if traded else 0.0,
                "risk_usd": risk_usd if traded else None,
                "R": round(pnl / risk_usd, 3) if traded and risk_usd else None})
    hit["move"] = move_of(entry if traded else hit.get("price_start"), exit if traded else price_end)
    grade(hit)
    Path(path).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return hit


def summary(rows: list[dict], venue: str = "all") -> dict:
    rows = [r for r in rows if venue == "all" or r.get("venue") == venue]
    trades = [r for r in rows if r.get("outcome") not in ("pending", "no trade")]
    wins = sum(1 for r in trades if (r.get("pnl") or 0) > 0)
    rs = [r["R"] for r in trades if isinstance(r.get("R"), (int, float))]
    bots = {}
    for b in (*WORKERS, "queen"):
        scored = [r for r in rows if (r.get("hits") or {}).get(b) is not None]
        n = len(scored)
        h = sum(r["hits"][b] for r in scored)
        bots[b] = {"n": n, "hits": h, "hit_rate": h / n if n else None, "small_sample": n < MIN_SAMPLE}
    return {"rounds": len(rows), "trades": len(trades), "wins": wins,
            "win_rate": wins / len(trades) if trades else None,
            "avg_R": sum(rs) / len(rs) if rs else None,
            "pnl": round(sum(r.get("pnl") or 0 for r in trades), 2),
            "small_sample": len(trades) < MIN_SAMPLE or any(v["small_sample"] for v in bots.values()),
            "bots": bots}
