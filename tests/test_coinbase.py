"""Coinbase Advanced Trade adapter tests. All offline: the SDK is mocked; no network, no keys, no real orders."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bridge.coinbase import (  # noqa: E402
    CoinbaseClient, CoinbaseError, floor_to_increment, fmt_dec, unescape_private_key,
)
from bridge.guard import GuardState  # noqa: E402
from bridge.server import create_app  # noqa: E402
from bridge.settings import LIVE_CONFIRM_PHRASE, SettingsError, load_settings  # noqa: E402

TOKEN = "test-bridge-token-0123456789"
KEY_NAME = "organizations/org-TEST/apiKeys/key-TEST"
PRIVATE_PEM = "-----BEGIN EC PRIVATE KEY-----\\nTESTKEYMATERIAL\\n-----END EC PRIVATE KEY-----\\n"

PRODUCTS = [
    {"product_id": "BTC-USD", "base_increment": "0.00000001", "quote_increment": "0.01",
     "base_min_size": "0.000016", "quote_min_size": "1.00", "base_max_size": "240", "status": "ONLINE"},
    {"product_id": "ETH-USD", "base_increment": "0.00000001", "quote_increment": "0.01",
     "base_min_size": "0.0001", "quote_min_size": "1.00", "base_max_size": "1600", "status": "ONLINE"},
    {"product_id": "XLM-USD", "base_increment": "1", "quote_increment": "0.000001",
     "base_min_size": "1", "quote_min_size": "1.00", "base_max_size": "500000", "status": "ONLINE"},
]


class FakeSDK:
    def __init__(self, usd=100.0, products=None):
        self.usd, self.products = usd, list(products or PRODUCTS)
        self.previewed, self.created, self.cancelled = [], [], []
        self.bids, self.asks = {}, {}

    def get_accounts(self, **kw):
        return {"accounts": [
            {"uuid": "usd-1", "currency": "USD", "available_balance": {"value": str(self.usd), "currency": "USD"},
             "hold": {"value": "0", "currency": "USD"}, "name": "USD Wallet"},
        ]}

    def get_products(self, product_type=None, **kw):
        return {"products": list(self.products)}

    def get_product(self, product_id, **kw):
        for p in self.products:
            if p["product_id"] == product_id:
                return dict(p)
        raise RuntimeError(f"unknown product {product_id}")

    def get_best_bid_ask(self, product_ids=None, **kw):
        pid = (product_ids or ["ETH-USD"])[0]
        ask, bid = self.asks.get(pid, "3000.00"), self.bids.get(pid, "2999.00")
        if pid == "BTC-USD":
            ask, bid = self.asks.get(pid, "90000.00"), self.bids.get(pid, "89990.00")
        if pid == "XLM-USD":
            ask, bid = self.asks.get(pid, "0.250000"), self.bids.get(pid, "0.249900")
        return {"pricebooks": [{"product_id": pid, "asks": [{"price": ask, "size": "1"}],
                                "bids": [{"price": bid, "size": "1"}]}]}

    def preview_order(self, **kw):
        self.previewed.append(kw)
        return {"order_total": "25.00", "errs": [], "commission_total": "0.15"}

    def create_order(self, **kw):
        self.created.append(kw)
        return {"success": True, "success_response": {"order_id": "cb-oid-1"}, "order_id": "cb-oid-1"}

    def cancel_orders(self, order_ids=None, **kw):
        self.cancelled.extend(order_ids or [])
        return {"results": [{"success": True, "order_id": i} for i in (order_ids or [])]}


def cb_settings(tmp_path, **env):
    base = {"BRIDGE_TOKEN": TOKEN, "BRIDGE_STATE_DIR": str(tmp_path / "state"),
            "BRIDGE_BROKER": "coinbase", "COINBASE_API_KEY_NAME": KEY_NAME,
            "COINBASE_API_PRIVATE_KEY": PRIVATE_PEM}
    return load_settings({**base, **env})


def make(tmp_path, sdk=None, **env):
    s = cb_settings(tmp_path, **env)
    sdk = sdk or FakeSDK()
    client = CoinbaseClient(s.coinbase_api_key_name, s.coinbase_api_private_key, sdk=sdk)
    return TestClient(create_app(s, client=client)), client, sdk, s


H = {"X-Bridge-Token": TOKEN}
BUY = {"symbol": "ETH", "side": "buy", "stop": 2940.0, "target": 3120.0, "entry_ref": 3000.0, "risk_pct": 1.0,
       "risk": {"status": "approve"}, "queen": {"call": "BUY — SIMULATION ONLY"}}
SHORT = {**BUY, "side": "sell", "stop": 3060.0, "target": 2880.0,
         "queen": {"call": "SELL/SHORT — SIMULATION ONLY"}}


def test_defaults_and_secrets_stay_out_of_repr(tmp_path):
    s = cb_settings(tmp_path)
    assert s.broker == "coinbase" and s.limits.max_position_usd == 25 and s.limits.daily_loss_stop_usd == 5
    assert s.limits.account_max_leverage == 1 and s.has_credentials
    assert KEY_NAME not in repr(s) and "TESTKEYMATERIAL" not in repr(s) and PRIVATE_PEM not in repr(s)
    # official SDK env aliases
    s2 = load_settings({"BRIDGE_TOKEN": TOKEN, "BRIDGE_BROKER": "coinbase",
                        "COINBASE_API_KEY": KEY_NAME, "COINBASE_API_SECRET": PRIVATE_PEM,
                        "BRIDGE_STATE_DIR": str(tmp_path / "b")})
    assert s2.coinbase_api_key_name == KEY_NAME and s2.has_coinbase_credentials
    # TradeLocker defaults are unchanged when Coinbase is not selected
    tl = load_settings({"BRIDGE_TOKEN": TOKEN})
    assert tl.broker == "tradelocker" and tl.limits.max_position_usd == 2000 and tl.limits.daily_loss_stop_usd is None
    with pytest.raises(SettingsError):
        load_settings({"BRIDGE_TOKEN": TOKEN, "BRIDGE_BROKER": "kraken"})
    with pytest.raises(SettingsError):
        cb_settings(tmp_path / "c", COINBASE_ACCOUNT_MAX_LEVERAGE="5")
    assert unescape_private_key(PRIVATE_PEM).startswith("-----BEGIN EC PRIVATE KEY-----")
    assert "\n" in unescape_private_key(PRIVATE_PEM)


def test_list_products_balances_and_bracket_payload(tmp_path):
    sdk = FakeSDK(usd=80.0)
    c = CoinbaseClient(KEY_NAME, PRIVATE_PEM, sdk=sdk)
    acct = c.login()
    assert acct["currency"] == "USD" and KEY_NAME not in repr(c) and "TESTKEY" not in repr(c)
    state = c.account_state()
    assert state["balance"] == 80.0
    eth = c.find_instrument("ETH")
    d = c.instrument_details(eth)
    assert eth["product_id"] == "ETH-USD"
    assert d["base_increment"] == "0.00000001" and d["minLot"] == 0.0001 and d["quoteMinSize"] == 1.0
    xlm = c.find_instrument("XLM-USD")
    assert xlm["name"] == "XLM-USD" and c.instrument_details(xlm)["base_increment"] == "1"
    q = c.quote(eth)
    assert q["ask"] == 3000.0 and q["bid"] == 2999.0
    # preview payload: market IOC buy + attached trigger_bracket_gtc (TP/SL), no size on the attached order
    c.preview_order(eth, "buy", 0.008, 2940.0, 3120.0)
    body = sdk.previewed[-1]
    assert body["product_id"] == "ETH-USD" and body["side"] == "BUY"
    assert body["order_configuration"] == {"market_market_ioc": {"base_size": "0.008"}}
    br = body["attached_order_configuration"]["trigger_bracket_gtc"]
    assert br == {"limit_price": "3120", "stop_trigger_price": "2940"} and "base_size" not in br
    c.preview_order(eth, "buy", 0.008, 2940.0, 3120.0, order_type="limit", limit_price=2995)
    lim = sdk.previewed[-1]["order_configuration"]
    assert "limit_limit_gtc" in lim and lim["limit_limit_gtc"]["limit_price"] == "2995"


def test_long_only_refusal_skips_preview_and_create(tmp_path):
    api, client, sdk, _ = make(tmp_path)
    r = api.post("/orders", json=SHORT, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "spot_long_only"
    assert sdk.previewed == [] and sdk.created == []
    with pytest.raises(CoinbaseError, match="long-only"):
        inst = client.find_instrument("ETH")
        client.preview_order(inst, "sell", 0.01, 3060, 2880)
    assert sdk.previewed == []
    with pytest.raises(CoinbaseError, match="long-only"):
        client.place_market_order(client.find_instrument("ETH"), "sell", 0.01, 3060, 2880)
    assert sdk.created == []


def test_size_rounds_down_never_up(tmp_path):
    # 0.008333... ETH at $25 cap / $3000 should floor to the increment, not round up
    stepped = floor_to_increment(0.0083333333, "0.00000001")
    assert fmt_dec(stepped) == "0.00833333"
    assert float(stepped) < 0.0083333333
    # Raise the % cap so the $25 notional cap binds: $25 / $3000 = 0.00833333..., floored to the increment.
    api, _, sdk, s = make(tmp_path, FakeSDK(usd=100.0), COINBASE_MAX_POSITION_PCT_OF_BALANCE="100")
    assert s.limits.max_position_usd == 25
    r = api.post("/orders", json=BUY, headers=H)
    assert r.status_code == 200, r.text
    p = r.json()["plan"]
    assert p["notional_usd"] <= 25 and p["lots"] == pytest.approx(0.00833333, rel=0, abs=1e-8)
    assert p["lots"] < 25 / 3000  # floored; never rounded up to the exact cap
    # below-min: $5 cash cannot reach XLM's 1-token min under a $25 cap? XLM is $0.25 so 1 token is fine.
    # Use a custom product with a huge min size via ETH details override: tiny account vs BTC min notional.
    small = FakeSDK(usd=5.0)
    # Make ETH require a 1.0 min lot so $25 cap at $3000 cannot reach it.
    small.products = [{**PRODUCTS[1], "base_min_size": "1", "base_increment": "0.01"}]
    api, _, sdk, _ = make(tmp_path / "s", small)
    r = api.post("/orders", json=BUY, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "below_min_lot"
    assert small.previewed == [] and small.created == []


def test_preview_only_never_calls_create(tmp_path):
    api, _, sdk, _ = make(tmp_path)
    st = api.get("/status", headers=H).json()
    assert st["broker_id"] == "coinbase" and st["mode"] == "PREVIEW" and st["preview_only"] is True
    assert st["connected"] is True and st["account"]["balance"] == 100
    r = api.post("/orders", json=BUY, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["placed"] is False and body["preview"] is True and body["mode"] == "PREVIEW"
    assert len(sdk.previewed) == 1 and sdk.created == []
    assert sdk.previewed[0]["attached_order_configuration"]["trigger_bracket_gtc"]["limit_price"] == "3120"
    # Arming without ALPHA_HIVE_ALLOW_LIVE=1 is refused; still no create
    assert api.post("/mode", json={"mode": "LIVE", "confirm": LIVE_CONFIRM_PHRASE}, headers=H).status_code == 403
    assert sdk.created == []
    # dry_run also stays on preview
    r = api.post("/orders", json={**BUY, "dry_run": True}, headers=H)
    assert r.json()["placed"] is False and sdk.created == []


def test_live_armed_calls_create_with_bracket(tmp_path):
    api, _, sdk, _ = make(tmp_path, ALPHA_HIVE_ALLOW_LIVE="1")
    assert api.get("/status", headers=H).json()["mode"] == "PREVIEW"
    r = api.post("/mode", json={"mode": "LIVE", "confirm": "yes"}, headers=H)
    assert r.status_code == 400
    assert api.post("/mode", json={"mode": "LIVE", "confirm": LIVE_CONFIRM_PHRASE}, headers=H).json()["mode"] == "LIVE"
    r = api.post("/orders", json=BUY, headers=H)
    assert r.status_code == 200 and r.json()["placed"] is True and r.json()["order_id"] == "cb-oid-1"
    assert len(sdk.created) == 1 and sdk.created[0]["side"] == "BUY"
    assert "market_market_ioc" in sdk.created[0]["order_configuration"]
    assert sdk.created[0]["attached_order_configuration"]["trigger_bracket_gtc"]["stop_trigger_price"] == "2940"
    # disarm returns to preview; close the open order so max_open_positions doesn't block the next call
    oid = r.json()["order_id"]
    assert api.delete(f"/positions/{oid}", headers=H).status_code == 200
    assert api.post("/mode", json={"mode": "PREVIEW"}, headers=H).json()["mode"] == "PREVIEW"
    sdk.previewed.clear(); sdk.created.clear()
    r = api.post("/orders", json=BUY, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["placed"] is False and sdk.previewed and sdk.created == []


@pytest.mark.parametrize("status", ["reject", "reduce"])
def test_risk_reject_still_blocks_without_preview_or_create(tmp_path, status):
    api, _, sdk, _ = make(tmp_path)
    r = api.post("/orders", json={**BUY, "risk": {"status": status}}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "risk_not_approved"
    assert sdk.previewed == [] and sdk.created == []


def test_guard_kill_and_daily_loss_usd_cap(tmp_path):
    api, _, sdk, s = make(tmp_path)
    r = api.post("/kill", json={"close_all": False, "reason": "test"}, headers=H)
    assert r.status_code == 200
    r = api.post("/orders", json=BUY, headers=H)
    assert r.status_code == 423 and sdk.previewed == [] and sdk.created == []
    assert GuardState(s.state_dir, s.limits).release_kill() is True
    # daily $5 cap: start at $100, drop to $94.9 (loss $5.10)
    api.get("/status", headers=H)
    # swap the client's cash by changing the sdk and re-logging
    sdk.usd = 94.9
    r = api.post("/orders", json=BUY, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "daily_loss_stop"
    assert sdk.previewed == [] and sdk.created == []
