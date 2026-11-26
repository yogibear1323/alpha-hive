"""Browser UI for the six-bot research team. Educational simulation only.

Start it from the project folder:
    python web/app.py            (then open http://127.0.0.1:8642)
    python web/app.py --port 9000

Uses the same agents/ code as run_demo.py (offline mock unless MODEL_PROVIDER=GROK).
Listens on 127.0.0.1 only. Refuses to start unless TRADING_MODE=SIMULATION_ONLY.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask, Response, abort, jsonify, request, send_file  # noqa: E402

from agents.base import (BotError, get_provider, load_config, load_market,  # noqa: E402
                         load_risk_rules, redact_obj)
from agents.pipeline import list_reports, report_path_for, run_pipeline, validate_packet  # noqa: E402

DATA_DIR = ROOT / "data"
TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"
VENDOR_DIR = ROOT / "terminal" / "vendor"
MAX_UPLOAD_BYTES = 1_000_000

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def _mode_ok() -> bool:
    return os.environ.get("TRADING_MODE", "").strip() == "SIMULATION_ONLY"


def render_page(boot: dict) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    return html.replace("/*__BOOT__*/null", json.dumps(boot, ensure_ascii=False))


@app.get("/")
def index():
    provider = os.environ.get("MODEL_PROVIDER", "MOCK").strip().upper() or "MOCK"
    config = load_config()
    rules = {k: v for k, v in load_risk_rules().items() if not k.startswith("_")}
    boot = {"mode": "server", "provider": provider, "model": config.get("model"),
            # demo sessions run the offline mock engine in the browser with these human rules
            "static": {"markets": [], "rules": rules,
                       "balance": config["simulation"]["starting_balance_usd"]}}
    return Response(render_page(boot), mimetype="text/html")



@app.get("/vendor/<path:name>")
def vendor(name):
    """Serve locally vendored browser scripts from terminal/vendor (no CDNs)."""
    p = (VENDOR_DIR / name).resolve()
    if p.parent != VENDOR_DIR.resolve() or not p.is_file() or p.suffix != ".js":
        abort(404)
    return send_file(p, mimetype="text/javascript; charset=utf-8")


@app.get("/api/markets")
def markets():
    items = []
    for p in sorted(DATA_DIR.glob("*.json")):
        try:
            data = load_market(p)
        except (OSError, ValueError):
            continue
        items.append({"file": p.name, "market": data.get("market"), "label": data.get("_label")})
    items.sort(key=lambda m: m["file"] != "sample_market.json")  # sample first
    return jsonify(items)


@app.get("/api/markets/<name>")
def market(name):
    p = (DATA_DIR / name).resolve()
    if p.parent != DATA_DIR.resolve() or not p.exists() or p.suffix != ".json":
        abort(404)
    return send_file(p, mimetype="application/json")


@app.get("/api/reports")
def reports():
    return jsonify(list_reports())


@app.get("/api/reports/<name>")
def report(name):
    p = report_path_for(name)
    if p is None:
        abort(404)
    return send_file(p, mimetype="application/json")


@app.post("/api/run")
def run():
    if not _mode_ok():
        return jsonify({"error": "TRADING_MODE must be SIMULATION_ONLY"}), 403
    body = request.get_json(silent=True) or {}
    market_path = None
    if "packet" in body:
        packet = body["packet"]
        market_path = f"uploaded:{str(body.get('filename') or 'packet.json')[:80]}"
    elif "market_file" in body:
        p = (DATA_DIR / str(body["market_file"])).resolve()
        if p.parent != DATA_DIR.resolve() or not p.exists():
            return jsonify({"error": "unknown market file"}), 400
        packet = load_market(p)
        market_path = str(p)
    else:
        return jsonify({"error": "send market_file or packet"}), 400
    problems = validate_packet(packet)
    if problems:
        return jsonify({"error": "; ".join(problems)}), 400

    config = load_config()
    try:
        provider = get_provider(config)
    except BotError as exc:
        return jsonify({"error": str(exc)}), 400

    events: queue.Queue = queue.Queue()

    def worker():
        try:
            run_pipeline(packet, provider, config, load_risk_rules(), on_event=events.put,
                         market_path=market_path)
        except Exception as exc:  # never leave the browser hanging
            events.put(redact_obj({"type": "error", "message": f"{type(exc).__name__}: {exc}"}))
            events.put({"type": "done", "final_result": "SKIP", "report_file": None, "report": None})

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            ev = events.get()
            yield json.dumps(ev, ensure_ascii=False) + "\n"
            if ev.get("type") == "done":
                break

    return Response(stream(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def main(argv=None) -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    if not _mode_ok():
        print("Refusing to start: TRADING_MODE must be SIMULATION_ONLY. Copy .env.example to .env.",
              file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="Six-bot team browser UI (simulation only)")
    parser.add_argument("--port", type=int, default=8642)
    args = parser.parse_args(argv)
    print(f"Six-bot team UI (SIMULATION ONLY) -> http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
