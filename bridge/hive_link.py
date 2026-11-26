"""Queen -> bridge link. Runs an ordinary hive round (agents.pipeline, unchanged and
order-free) and forwards the result to the local bridge ONLY when the Risk guard approved
and the Queen made a directional call. A Risk reject or reduce never produces an order."""
from __future__ import annotations

import copy

import requests

from bridge import levels

BUY, SHORT = "BUY — SIMULATION ONLY", "SELL/SHORT — SIMULATION ONLY"


class BridgeHTTP:
    def __init__(self, url: str = "http://127.0.0.1:8765", token: str = "", session=None):
        if not url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("the bridge must be a local URL (http://127.0.0.1 or http://localhost)")
        self.url, self.token, self.http = url.rstrip("/"), token, session or requests.Session()

    def send_order(self, payload: dict) -> dict:
        r = self.http.post(f"{self.url}/orders", json=payload, headers={"X-Bridge-Token": self.token}, timeout=30)
        try:
            body = r.json()
        except ValueError:
            body = {"detail": r.text[:200]}
        return {"http_status": r.status_code, **(body if isinstance(body, dict) else {"body": body})}


def order_from_report(report: dict, packet: dict, symbol: str | None = None, risk_pct: float | None = None):
    """Returns (payload, None) or (None, reason). Reads a COPY of the Risk verdict."""
    risk = copy.deepcopy((report.get("outputs") or {}).get("risk") or {})
    call = report.get("final_result")
    if report.get("stopped_at") or report.get("error"):
        return None, f"round stopped ({report.get('stopped_at') or report.get('error')})"
    if risk.get("status") != "approve":
        return None, f"Risk said '{risk.get('status')}': no order"
    if call not in (BUY, SHORT):
        return None, f"the Queen's call is {call}: no order"
    price = (packet.get("price") or {}).get("last")
    direction = 1 if call == BUY else -1
    a = levels.atr(packet)
    if not price or not a:
        return None, "no price or ATR to set the stop and target"
    stop, target, _ = levels.stop_target(float(price), direction, a)
    return {"symbol": symbol or packet.get("market"), "side": "buy" if direction > 0 else "sell",
            "stop": round(stop, 8), "target": round(target, 8), "entry_ref": float(price),
            "risk_pct": risk_pct, "risk": {"status": risk["status"]}, "queen": {"call": call},
            "client_ref": f"hive-{report.get('run_id', '')}"[:60]}, None


def route_report(report: dict, packet: dict, bridge, symbol: str | None = None, risk_pct: float | None = None,
                 dry_run: bool = False) -> dict:
    payload, why = order_from_report(report, packet, symbol, risk_pct)
    if payload is None:
        return {"sent": False, "reason": why}
    payload["dry_run"] = dry_run
    return {"sent": True, "payload": payload, "bridge": bridge.send_order(payload)}


def run_and_route(packet, provider, bridge, config=None, rules=None, symbol=None, risk_pct=None, dry_run=False):
    from agents.pipeline import run_pipeline
    report = run_pipeline(packet, provider, config, rules)
    report["execution"] = route_report(report, packet, bridge, symbol, risk_pct, dry_run)
    return report
