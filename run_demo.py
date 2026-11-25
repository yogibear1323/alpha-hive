"""Run the six-bot research team once on a market packet.

    python run_demo.py
    python run_demo.py --market data/my_market.json

Educational simulation only. Refuses to start unless TRADING_MODE=SIMULATION_ONLY.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")  # real environment variables win over .env
    except ImportError:
        pass

    mode = os.environ.get("TRADING_MODE", "").strip()
    if mode != "SIMULATION_ONLY":
        print(f"Refusing to start: TRADING_MODE must be SIMULATION_ONLY (got '{mode or 'unset'}').\n"
              "Copy .env.example to .env, or set TRADING_MODE=SIMULATION_ONLY.", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description="Six-bot research team (simulation only)")
    parser.add_argument("--market", default=str(ROOT / "data" / "sample_market.json"),
                        help="path to a market packet JSON (keep the _label field)")
    args = parser.parse_args(argv)

    from agents.base import BotError, get_provider, load_config, load_market, load_risk_rules
    from agents.pipeline import run_pipeline

    config = load_config()
    try:
        provider = get_provider(config)
    except BotError as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 1
    try:
        packet = load_market(args.market)
    except (OSError, ValueError) as exc:
        print(f"Could not read market packet {args.market}: {exc}", file=sys.stderr)
        return 1

    print(f"Six-bot team | provider={provider.name} | market={packet.get('market')} | SIMULATION ONLY")
    label = str(packet.get("_label", ""))
    if label:
        print(f"Data label: {label}")

    def on_event(ev):
        if ev["type"] == "step_done":
            print(f"[{ev['step']}/6] {ev['label']} Bot complete")
        elif ev["type"] == "stopped":
            print(f"Stopped: {ev['reason']}")
        elif ev["type"] == "error":
            print(f"Stopped safely: {ev['message']}")

    report = run_pipeline(packet, provider, config, load_risk_rules(), on_event=on_event,
                          market_path=args.market)
    risk = report["outputs"].get("risk")
    if risk:
        print(f"Risk gate: {risk['status']} (max simulated size ${risk['maximum_simulated_size']:g}, "
              f"max simulated loss ${risk['maximum_simulated_loss']:g})")
    print(f"Final result: {report['final_result']}")
    print("Human review required. No order was placed.")
    print(f"Report saved -> {report['report_file']}")
    print(f"Log saved    -> {report['log_file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
