"""Broker bridge tests. All offline: TradeLocker is mocked; nothing touches the network."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agents.base import MockProvider, load_config, load_market, load_risk_rules  # noqa: E402
from bridge import hive_link, levels  # noqa: E402
from bridge.guard import GuardState  # noqa: E402
from bridge.server import create_app  # noqa: E402
from bridge.settings import LIVE_CONFIRM_PHRASE, Limits, SettingsError, load_settings  # noqa: E402
from bridge.tradelocker import TradeLockerClient, TradeLockerError  # noqa: E402

PASSWORD = "pw-NEVER-LOGGED-123"
TOKEN = "test-bridge-token-0123456789"
BASE = "https://demo.tradelocker.com/backend-api"
INSTRUMENTS = [
    {"name": "BTCUSD", "tradableInstrumentId": 25502, "routes": [{"id": 11, "type": "TRADE"}, {"id": 12, "type": "INFO"}]},
    {"name": "ETHUSD", "tradableInstrumentId": 25508, "routes": [{"id": 11, "type": "TRADE"}, {"id": 12, "type": "INFO"}]},
    {"name": "US30", "tradableInstrumentId": 25638, "routes": [{"id": 21, "type": "TRADE"}, {"id": 22, "type": "INFO"}]},
    {"name": "BTCEUR", "tradableInstrumentId": 25503, "routes": [{"id": 11, "type": "TRADE"}]},
]


# ---------------- a fake HTTP session for the real TradeLockerClient ----------------
class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        return self.request("POST", url, json=json, headers=headers or {})

    def get(self, url, headers=None, timeout=None):
        return self.request("GET", url, headers=headers or {})

    def request(self, method, url, headers=None, timeout=None, json=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "json": json})
        path = url.replace(BASE, "")
        if path == "/auth/jwt/token":
            ok = json == {"email": "robert@example.com", "password": PASSWORD, "server": "CRUC"}
            return Resp(200, {"accessToken": "ACCESS-JWT", "refreshToken": "REFRESH-JWT"}) if ok else Resp(401, {})
        if path == "/auth/jwt/all-accounts":
            return Resp(200, {"accounts": [{"id": "2654457", "accNum": "1", "currency": "USD", "status": "ACTIVE"}]})
        if path.endswith("/instruments"):
            return Resp(200, {"s": "ok", "d": {"instruments": INSTRUMENTS}})
        if path.endswith("/orders"):
            return Resp(200, {"s": "ok", "d": {"orderId": "777"}})
        return Resp(404, {})


def client(session=None):
    return TradeLockerClient(BASE, "robert@example.com", PASSWORD, "CRUC", session=session or FakeSession())


# ---------------- a fake broker for the server ----------------
class FakeTL:
    def __init__(self, balance=10000.0, positions=None, today_net=0.0, ask=3000.0, bid=2999.0):
        self.balance, self._pos, self.today_net, self.ask, self.bid = balance, list(positions or []), today_net, ask, bid
        self.placed, self.closed, self.authed, self.account = [], [], True, {"id": "1", "accNum": "1"}

    def login(self):
        return self.account

    def account_state(self):
        return {"balance": self.balance, "projectedBalance": self.balance, "todayNet": self.today_net}

    def positions(self):
        return list(self._pos)

    def instruments(self):
        return INSTRUMENTS

    def find_instrument(self, s):
        return client().find_instrument.__func__(self, s)

    def instrument_details(self, inst):
        return {"lotSize": 1, "lotStep": 0.01, "minLot": 0.01, "maxLot": 100, "leverage": 500}

    def quote(self, inst):
        return {"ask": self.ask, "bid": self.bid}

    def place_market_order(self, inst, side, lots, sl, tp):
        self.placed.append({"inst": inst["name"], "side": side, "lots": lots, "sl": sl, "tp": tp})
        self._pos.append({"id": "P1", "tradableInstrumentId": inst["tradableInstrumentId"]})
        return "777"

    def find_new_position(self, inst, known):
        return "P1"

    def close_position(self, pid):
        self.closed.append(pid)


def make(tmp_path, fake=None, **env):
    base = {"BRIDGE_TOKEN": TOKEN, "BRIDGE_STATE_DIR": str(tmp_path / "state"), "TRADELOCKER_EMAIL": "robert@example.com",
            "TRADELOCKER_PASSWORD": PASSWORD, "TRADELOCKER_SERVER": "CRUC"}
    s = load_settings({**base, **env})
    fake = fake or FakeTL()
    return TestClient(create_app(s, client=fake)), fake, s


H = {"X-Bridge-Token": TOKEN}
ORDER = {"symbol": "ETH", "side": "buy", "stop": 2940.0, "target": 3120.0, "entry_ref": 3000.0, "risk_pct": 1.0,
         "risk": {"status": "approve"}, "queen": {"call": "BUY — SIMULATION ONLY"}}


# 1 auth
def test_auth_login_selects_account_and_hides_secrets():
    sess = FakeSession(); c = client(sess)
    acct = c.login()
    assert acct["id"] == "2654457"
    login = sess.calls[0]
    assert login["url"] == f"{BASE}/auth/jwt/token" and login["json"]["server"] == "CRUC"
    c.instruments()
    trade = sess.calls[-1]
    assert trade["headers"]["Authorization"] == "Bearer ACCESS-JWT" and trade["headers"]["accNum"] == "1"
    assert PASSWORD not in repr(c) and "ACCESS-JWT" not in repr(c)
    s = load_settings({"TRADELOCKER_PASSWORD": PASSWORD, "BRIDGE_TOKEN": TOKEN})
    assert PASSWORD not in repr(s) and TOKEN not in repr(s)
    bad = TradeLockerClient(BASE, "robert@example.com", "wrong", "CRUC", session=FakeSession())
    with pytest.raises(TradeLockerError) as e:
        bad.login()
    assert "wrong" not in str(e.value)


# 2 instrument lookup
def test_instrument_lookup_btc_eth_us30():
    c = client()
    btc, eth, us30 = (c.find_instrument(s) for s in ("BTCUSD", "ETH/USD", "us30"))
    assert (btc["tradableInstrumentId"], btc["trade_route"], btc["info_route"]) == (25502, 11, 12)
    assert eth["name"] == "ETHUSD" and us30["tradableInstrumentId"] == 25638
    with pytest.raises(TradeLockerError):
        c.find_instrument("DOGEUSD")


# 3 order payload with one SL and one TP
def test_market_order_carries_sl_and_tp():
    sess = FakeSession(); c = client(sess)
    oid = c.place_market_order(c.find_instrument("BTCUSD"), "buy", 0.02, 89000.0, 93000.0)
    body = sess.calls[-1]["json"]
    assert oid == "777" and sess.calls[-1]["url"].endswith("/trade/accounts/2654457/orders")
    assert body["type"] == "market" and body["validity"] == "IOC" and body["qty"] == 0.02
    assert (body["stopLoss"], body["stopLossType"], body["takeProfit"], body["takeProfitType"]) == \
        (89000.0, "absolute", 93000.0, "absolute")


# 4 server sizing inside the hard limits
def test_server_places_order_with_sl_tp_inside_limits(tmp_path):
    api, fake, s = make(tmp_path)
    r = api.post("/orders", json=ORDER, headers=H)
    assert r.status_code == 200, r.text
    p = r.json()["plan"]
    # risk capped at MAX_RISK_PCT_PER_TRADE 0.5% -> $50 / $60 stop = 0.83 ETH; notional capped at $2,000 -> 0.66 lot
    assert p["risk_pct"] == 0.5 and p["lots"] == 0.66 and p["capped"] and p["notional_usd"] <= 2000
    assert fake.placed == [{"inst": "ETHUSD", "side": "buy", "lots": 0.66, "sl": 2940.0, "tp": 3120.0}]
    assert r.json()["position_id"] == "P1"


# 5 Risk reject (or reduce) blocks the order
@pytest.mark.parametrize("status", ["reject", "reduce"])
def test_risk_not_approved_blocks_order(tmp_path, status):
    api, fake, _ = make(tmp_path)
    r = api.post("/orders", json={**ORDER, "risk": {"status": status}}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "risk_not_approved"
    assert fake.placed == []


def test_queen_round_with_risk_reject_never_reaches_bridge(tmp_path):
    packet = load_market(ROOT / "data" / "sample_market.json")
    cfg = load_config(); cfg["reports_dir"] = str(tmp_path / "r"); cfg["logs_dir"] = str(tmp_path / "l")
    reject = lambda p: {"status": "reject", "maximum_simulated_size": 0, "maximum_simulated_loss": 0,  # noqa: E731
                        "risk_flags": ["test"], "reason": "test reject"}
    greedy = lambda p: {"market": "EXMP", "decision": "BUY — SIMULATION ONLY", "confidence": 99,  # noqa: E731
                        "reasons": ["buy anyway"], "missing_data": [], "conflicting_signals": [],
                        "human_review_required": False}

    class Spy:
        sent = []

        def send_order(self, payload):
            self.sent.append(payload); return {"http_status": 200}
    spy = Spy()
    rep = hive_link.run_and_route(packet, MockProvider(overrides={"risk": reject, "decision": greedy}), spy, cfg,
                                  load_risk_rules(), symbol="ETH")
    assert rep["final_result"] == "SKIP" and rep["execution"]["sent"] is False and spy.sent == []
    # a forged report that claims BUY while Risk rejected is still refused
    forged = {"final_result": "BUY — SIMULATION ONLY", "outputs": {"risk": {"status": "reject"}}}
    assert hive_link.route_report(forged, packet, spy)["sent"] is False and spy.sent == []


def test_risk_approved_round_routes_with_atr_levels():
    packet = {"market": "ETH", "price": {"last": 3000.0},
              "intraday_bars": [{"high": 3010.0, "low": 2990.0}] * 14}
    rep = {"final_result": "BUY — SIMULATION ONLY", "outputs": {"risk": {"status": "approve"}}, "run_id": "x"}
    payload, why = hive_link.order_from_report(rep, packet, "ETH", 1.0)
    assert why is None and payload["side"] == "buy"
    assert payload["stop"] == pytest.approx(2970.0) and payload["target"] == pytest.approx(3060.0)  # 1.5 x ATR 20, 2:1
    assert levels.stop_target(100.0, -1, 0.001)[2] == pytest.approx(0.06)  # 0.06% floor


# 6 kill switch
def test_kill_switch_blocks_new_orders_and_only_releases_locally(tmp_path):
    api, fake, s = make(tmp_path, FakeTL(positions=[{"id": "OLD", "tradableInstrumentId": 25508}]))
    r = api.post("/kill", json={"close_all": True, "reason": "test"}, headers=H)
    assert r.status_code == 200 and r.json()["closed"] == ["OLD"]
    fake._pos = []
    r = api.post("/orders", json=ORDER, headers=H)
    assert r.status_code == 423 and fake.placed == []
    assert api.post("/kill/release", headers=H).status_code in (404, 405)   # no HTTP release route
    assert GuardState(s.state_dir, s.limits).release_kill() is True
    assert api.post("/orders", json=ORDER, headers=H).status_code == 200
    api2, fake2, _ = make(tmp_path / "b", KILL_SWITCH="1")
    assert api2.post("/orders", json=ORDER, headers=H).status_code == 423 and fake2.placed == []


# 7 daily loss stop
def test_daily_loss_stop_blocks_and_latches(tmp_path):
    fake = FakeTL(balance=10000.0)
    api, _, _ = make(tmp_path, fake)
    api.get("/status", headers=H)                      # records the day's starting equity
    fake.balance = 9790.0                              # -2.1% >= 2% limit
    r = api.post("/orders", json=ORDER, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "daily_loss_stop" and fake.placed == []
    fake.balance = 10000.0                             # latched for the rest of the UTC day
    assert api.post("/orders", json=ORDER, headers=H).json()["detail"]["blocked"] == "daily_loss_stop"


def test_max_open_positions_and_min_lot_never_rounds_up(tmp_path):
    api, fake, _ = make(tmp_path, FakeTL(positions=[{"id": "A", "tradableInstrumentId": 1}]))
    assert api.post("/orders", json=ORDER, headers=H).json()["detail"]["blocked"] == "max_open_positions"
    small = FakeTL(balance=500.0, ask=90000.0, bid=89990.0)       # $500 account: 0.01 BTC lot is $900 notional
    api, fake, _ = make(tmp_path / "s", small)
    btc = {**ORDER, "symbol": "BTC", "stop": 89000.0, "target": 92000.0, "entry_ref": 90000.0}
    r = api.post("/orders", json=btc, headers=H)
    assert r.json()["detail"]["blocked"] == "below_min_lot" and small.placed == []


# 8 auth, CORS, modes
def test_token_cors_and_live_gates(tmp_path):
    api, fake, _ = make(tmp_path)
    assert api.post("/orders", json=ORDER).status_code == 401
    assert api.get("/health").status_code == 200
    ok = api.options("/orders", headers={"Origin": "http://127.0.0.1:8642", "Access-Control-Request-Method": "POST"})
    bad = api.options("/orders", headers={"Origin": "https://yogibear1323.github.io", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:8642"
    assert "access-control-allow-origin" not in bad.headers
    assert api.post("/mode", json={"mode": "LIVE", "confirm": LIVE_CONFIRM_PHRASE}, headers=H).status_code == 403
    with pytest.raises(SettingsError):
        load_settings({"TRADELOCKER_ENV": "live", "BRIDGE_TOKEN": TOKEN})          # live without the env flag
    api, fake, _ = make(tmp_path / "l", TRADELOCKER_ENV="live", ALPHA_HIVE_ALLOW_LIVE="1")
    assert api.post("/orders", json=ORDER, headers=H).json()["detail"]["blocked"] == "live_not_armed"
    assert api.post("/mode", json={"mode": "LIVE", "confirm": "yes"}, headers=H).status_code == 400
    assert api.post("/mode", json={"mode": "LIVE", "confirm": LIVE_CONFIRM_PHRASE}, headers=H).json()["mode"] == "LIVE"
    assert api.post("/orders", json=ORDER, headers=H).status_code == 200


def test_limits_cannot_exceed_code_ceilings():
    with pytest.raises(SettingsError):
        load_settings({"MAX_RISK_PCT_PER_TRADE": "5", "BRIDGE_TOKEN": TOKEN})
    with pytest.raises(SettingsError):
        load_settings({"ACCOUNT_MAX_LEVERAGE": "500", "BRIDGE_TOKEN": TOKEN})
    assert Limits().account_max_leverage == 30
