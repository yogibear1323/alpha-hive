"""REAL testnet end-to-end for the Live session order desk. Opt-in: ALPHA_HIVE_TESTNET_E2E=1.

Friendbot funds a throwaway bot keypair generated in memory; its secret goes only into a
temporary bridge .env inside pytest's tmp dir (deleted with it) and is never printed.
Flow: trustline -> convert some XLM to USDC -> arm testnet -> Queen LONG (market) fills ->
limit offer rests -> cancel -> another offer -> kill switch cancels it and disarms.
Never touches mainnet: the settings are pinned to STELLAR_NETWORK=testnet and asserted.

Run: ALPHA_HIVE_TESTNET_E2E=1 python -m pytest -q -s tests/test_stellar_orders_testnet_e2e.py
"""
from __future__ import annotations

import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from stellar_sdk import Keypair

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.server import create_app  # noqa: E402
from bridge.settings import load_settings  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("ALPHA_HIVE_TESTNET_E2E") != "1",
                                reason="set ALPHA_HIVE_TESTNET_E2E=1 to run the real testnet e2e")

TOKEN = "e2e-bridge-token-0123456789"
PASS = "e2e-testnet-passphrase"


def _ok(r, what):
    assert r.status_code == 200, f"{what}: {r.status_code} {r.text[:400]}"
    return r.json()


def test_live_session_orders_on_testnet(tmp_path):
    bot = Keypair.random()
    fb = httpx.get("https://friendbot.stellar.org", params={"addr": bot.public_key}, timeout=60)
    assert fb.status_code == 200, "friendbot failed"
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"BRIDGE_TOKEN={TOKEN}\n", encoding="utf-8")
    s = load_settings({"BRIDGE_TOKEN": TOKEN, "BRIDGE_STATE_DIR": str(tmp_path / "state"),
                       "TRADELOCKER_EMAIL": "e2e@example.com", "TRADELOCKER_PASSWORD": "x",
                       "TRADELOCKER_SERVER": "demo", "STELLAR_NETWORK": "testnet", "ALPHA_HIVE_ALLOW_LIVE": "0",
                       "STELLAR_BOT_PUBLIC_KEY": bot.public_key, "STELLAR_BOT_SECRET": bot.secret},
                      dotenv_path=dotenv)
    assert s.stellar_network == "testnet" and not s.allow_live
    c = TestClient(create_app(s))
    _ok(c.post("/setup/passphrase", json={"passphrase": PASS}), "passphrase")
    h = {"X-Session-Token": _ok(c.post("/unlock", json={"passphrase": PASS}), "unlock")["token"]}
    log = []

    def note(label, d):
        txh = d.get("tx_hash") or ((d.get("result") or {}).get("hash") if isinstance(d.get("result"), dict) else None)
        log.append((label, txh))
        print(f"  {label}: {txh}")

    print(f"\n  bot account (testnet, throwaway): {bot.public_key}")
    tl = _ok(c.post("/stellar/trustline", json={"signer": "bot", "confirm": True}, headers=h), "trustline")
    assert tl["submitted"] is True
    note("USDC trustline", tl)
    cv = _ok(c.post("/stellar/convert", json={"direction": "xlm_to_usdc", "send_amount": "150", "slippage_pct": 1.0,
                                              "confirm": True, "signer": "bot"}, headers=h), "convert")
    assert cv["submitted"] is True
    note("convert 150 XLM -> USDC", cv)

    # Not armed yet: a Queen signal is preview only.
    snap = _ok(c.get("/stellar/orders", headers=h), "snapshot")
    ask = snap["prices"]["ask"]
    assert ask and ask > 0
    order = {"symbol": "XLM-USDC", "side": "buy", "entry_ref": ask, "stop": round(ask * 0.97, 7),
             "target": round(ask * 1.06, 7), "risk_pct": 0.5, "risk": {"status": "approve"},
             "queen": {"call": "LONG"}, "client_ref": "e2e-round-1", "slippage_pct": 1.0}
    pv = _ok(c.post("/stellar/orders", json={**order, "entry": ask, "balance": 50}, headers=h), "preview")
    assert pv["preview"] is True and pv["submitted"] is False

    _ok(c.post("/stellar/mode", json={"mode": "TESTNET"}, headers=h), "arm testnet")
    m = _ok(c.post("/stellar/orders", json=order, headers=h), "market order")
    print(f"  market plan: {m['plan']['units']} XLM, ${m['plan']['notional_usd']}")
    assert m["submitted"] is True and m["order"]["status"] == "filled", m
    assert m["plan"]["notional_usd"] <= 25
    note("Queen LONG market buy (pathPaymentStrictSend)", m["order"])

    lim = {**order, "order_type": "limit", "limit_price": round(ask * 0.5, 7), "client_ref": "e2e-limit-1"}
    lo = _ok(c.post("/stellar/orders", json=lim, headers=h), "limit order")
    assert lo["order"]["status"] == "open" and lo["order"]["offer_id"], lo
    note(f"limit buy rests (offer {lo['order']['offer_id']})", lo["order"])
    snap = _ok(c.get("/stellar/orders", headers=h), "snapshot")
    assert any(int(o["id"]) == int(lo["order"]["offer_id"]) for o in snap["open_offers"])
    cx = _ok(c.post(f"/stellar/orders/{lo['order']['id']}/cancel", headers=h), "cancel")
    assert cx["ok"] and cx["cancelled"] == [lo["order"]["offer_id"]]
    note("cancel offer", cx)
    time.sleep(1)
    snap = _ok(c.get("/stellar/orders", headers=h), "snapshot")
    assert snap["open_offers"] == []
    assert next(o for o in snap["orders"] if o["id"] == lo["order"]["id"])["status"] == "cancelled"

    lo2 = _ok(c.post("/stellar/orders", json={**lim, "client_ref": "e2e-limit-2"}, headers=h), "limit order 2")
    assert lo2["order"]["status"] == "open"
    note(f"limit buy rests (offer {lo2['order']['offer_id']})", lo2["order"])
    k = _ok(c.post("/stellar/kill", json={"reason": "e2e"}, headers=h), "kill")
    assert k["kill_switch"] and k["disarmed"] and k["cancel"]["cancelled"] == [lo2["order"]["offer_id"]]
    note("kill switch cancel-all", k["cancel"])
    sess = _ok(c.get("/session", headers=h), "session")
    assert sess["stellar_testnet_armed"] is False
    assert c.post("/stellar/mode", json={"mode": "TESTNET"}, headers=h).status_code == 423
    blocked = c.post("/stellar/orders", json=order, headers=h)
    assert blocked.status_code == 403   # preview path refuses while the kill switch is latched
    snap = _ok(c.get("/stellar/orders", headers=h), "snapshot")
    assert snap["open_offers"] == [] and snap["kill_switch"] is True
    print(f"  daily: loss ${snap['daily']['loss_usd']} of ${snap['daily']['limit_usd']} · pnl {snap['pnl']}")
    assert bot.secret not in str(snap) and bot.secret not in (s.state_dir / "orders.jsonl").read_text()
