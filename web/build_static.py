"""Rebuild web/static-demo.html: the same page with the sample data and the offline
mock engine baked in, so it opens straight from disk with no server.

    python web/build_static.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.base import load_config, load_risk_rules  # noqa: E402

WEB = Path(__file__).resolve().parent


def build() -> Path:
    markets = []
    for p in sorted((ROOT / "data").glob("*.json")):
        markets.append({"file": p.name, "packet": json.loads(p.read_text(encoding="utf-8"))})
    # sample first
    markets.sort(key=lambda m: m["file"] != "sample_market.json")
    rules = {k: v for k, v in load_risk_rules().items() if not k.startswith("_")}
    boot = {
        "mode": "static",
        "static": {
            "markets": markets,
            "rules": rules,
            "balance": load_config()["simulation"]["starting_balance_usd"],
        },
    }
    html = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
    payload = json.dumps(boot, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__BOOT__*/null", payload)
    out = WEB / "static-demo.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(f"Wrote {build()}")
