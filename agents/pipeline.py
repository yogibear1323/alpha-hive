"""Runs one hive round (led by the Queen) and writes the timestamped report and log.

Shared by run_demo.py (terminal) and web/app.py (browser UI).
"""
from __future__ import annotations

import json
from pathlib import Path

from agents.queen import Queen
from agents.base import (ROOT, BotError, RiskRejectedError, RunLogger, load_config,
                         load_risk_rules, redact_obj, timestamp)

STEPS = [
    (1, "Scout", "scout"),
    (2, "News", "news"),
    (3, "Sentiment", "sentiment"),
    (4, "Charts", "charts"),
    (5, "Risk", "risk"),
    (6, "Queen", "decision"),
]

DISCLAIMER = ("Educational simulation only. Not financial advice. No order was placed; "
              "this project cannot place one.")


def _rel(path):
    if not path:
        return path
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def validate_packet(packet) -> list[str]:
    problems = []
    if not isinstance(packet, dict):
        return ["market packet must be a JSON object"]
    for key in ("_label", "market"):
        if key not in packet:
            problems.append(f"market packet is missing '{key}'")
    return problems


def run_pipeline(packet: dict, provider, config: dict | None = None, rules: dict | None = None,
                 on_event=None, market_path: str | None = None) -> dict:
    """Run the team. on_event(dict) receives progress events. Returns the report dict."""
    config = config or load_config()
    rules = rules or load_risk_rules()
    run_id = timestamp()
    reports_dir = ROOT / config.get("reports_dir", "reports")
    logs_dir = ROOT / config.get("logs_dir", "logs")
    reports_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"run-{run_id}.log"
    report_path = reports_dir / f"report-{run_id}.json"
    logger = RunLogger(log_path)

    def emit(event: dict):
        if on_event:
            on_event(redact_obj(event))

    outputs: dict = {}
    report = {
        "run_id": run_id,
        "trading_mode": "SIMULATION_ONLY",
        "provider": getattr(provider, "name", type(provider).__name__),
        "model": config.get("model") if getattr(provider, "name", "") == "GROK" else "offline-mock",
        "market": packet.get("market") if isinstance(packet, dict) else None,
        "market_label": packet.get("_label") if isinstance(packet, dict) else None,
        "market_file": _rel(market_path),
        "outputs": outputs,
        "final_result": "SKIP",
        "stopped_at": None,
        "error": None,
        "human_review_required": True,
        "guard_rule": "Risk is the hive's guard and answers to no one: the Queen cannot override or bypass a Risk reject or reduce.",
        "disclaimer": DISCLAIMER,
    }
    logger.log(f"run {run_id} start provider={report['provider']} market={report['market']}")
    emit({"type": "start", "run_id": run_id, "market": report["market"],
          "label": report["market_label"], "provider": report["provider"]})

    try:
        problems = validate_packet(packet)
        if problems:
            raise BotError("; ".join(problems))
        queen = Queen(provider, rules, config, logger, emit)
        report["hierarchy"] = queen.hierarchy
        report["delegation"] = queen.ledger
        final, stopped = queen.hold_round(packet, outputs)
        report["final_result"] = final
        report["stopped_at"] = stopped
    except (BotError, RiskRejectedError) as exc:
        report["final_result"] = "SKIP"
        report["error"] = str(exc)
        report["stopped_at"] = report["stopped_at"] or "error"
        logger.log(f"STOPPED: {exc}")
        emit({"type": "error", "message": str(exc)})

    logger.log(f"Final result: {report['final_result']}")
    report["report_file"] = _rel(report_path)
    report["log_file"] = _rel(log_path)
    safe = redact_obj(report)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2, ensure_ascii=False)
    emit({"type": "done", "final_result": report["final_result"],
          "report_file": safe["report_file"], "report": safe})
    return safe


def list_reports(config: dict | None = None) -> list[dict]:
    config = config or load_config()
    reports_dir = ROOT / config.get("reports_dir", "reports")
    items = []
    for p in sorted(reports_dir.glob("report-*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append({"file": p.name, "run_id": data.get("run_id"), "market": data.get("market"),
                      "final_result": data.get("final_result"), "provider": data.get("provider"),
                      "risk_status": (data.get("outputs", {}).get("risk") or {}).get("status")})
    return items


def report_path_for(name: str, config: dict | None = None) -> Path | None:
    config = config or load_config()
    reports_dir = (ROOT / config.get("reports_dir", "reports")).resolve()
    if not name.startswith("report-") or not name.endswith(".json") or "/" in name or "\\" in name:
        return None
    p = (reports_dir / name).resolve()
    return p if p.parent == reports_dir and p.exists() else None
