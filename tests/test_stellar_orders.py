"""Live session order desk: sizing, caps, kill switch, daily loss, mainnet gate and submission.

Horizon is mocked: FakeHorizon decodes each signed envelope, checks it is signed by the bot
account for the right network, and answers with a real TransactionResult XDR. Nothing here
touches the network.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stellar_sdk import Asset, Keypair, TransactionEnvelope
from stellar_sdk import xdr as X

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.server import create_app  # noqa: E402
from bridge.settings import LIVE_CONFIRM_PHRASE, load_settings  # noqa: E402
from bridge.stellar_dex import network_passphrase, usdc_issuer  # noqa: E402
from bridge.stellar_orders import (  # noqa: E402
    Desk, Ledger, OrderError, amt, make_fill, parse_tx_result, pnl_summary, utc_day,
)

TOKEN = "test-bridge-token-0123456789"
PASS = "correct-horse-test-phrase"
ORDER = {"symbol": "XLM-USDC", "side": "buy", "entry_ref": 0.25, "stop": 0.24, "target": 0.27,
         "risk_pct": 0.5, "risk": {"status": "approve"}, "queen": {"call": "LONG"}}


# ---------------------------------------------------------------- result XDR builders
def _i64(n):
    return X.Int64(int(n))


def _amt(d):
    return _i64(Decimal(str(d)) * 10**7)


def _tx_result(op_tr, fee=100):
    return X.TransactionResult(
        fee_charged=_i64(fee),
        result=X.TransactionResultResult(X.TransactionResultCode.txSUCCESS,
                                         results=[X.OperationResult(X.OperationResultCode.opINNER, tr=op_tr)]),
        ext=X.TransactionResultExt(0)).to_xdr()


def _claim(seller, sold_asset, sold, bought_asset, bought, offer_id=1):
    return X.ClaimAtom(X.ClaimAtomType.CLAIM_ATOM_TYPE_ORDER_BOOK, order_book=X.ClaimOfferAtom(
        seller_id=Keypair.from_public_key(seller).xdr_account_id(), offer_id=_i64(offer_id),
        asset_sold=sold_asset.to_xdr_object(), amount_sold=_amt(sold),
        asset_bought=bought_asset.to_xdr_object(), amount_bought=_amt(bought)))


class FakeHorizon:
    """Enough of Horizon for the desk. Prices are USDC per XLM."""

    def __init__(self, network, bot_public, xlm="500", usdc="100", bid="0.249", ask="0.251"):
        self.network = network
        self.pub = bot_public
        self.usdc = Asset("USDC", usdc_issuer(network))
        self.xlm_bal, self.usdc_bal = Decimal(xlm), Decimal(usdc)
        self.bid, self.ask = Decimal(bid), Decimal(ask)
        self.offers = {}
        self.trades = {}
        self.submitted = []
        self.seq = 1000
        self.next_offer = 7000
        self.fail_next = None
        self.maker = Keypair.random().public_key
        self.path_rate = None   # override USDC-per-XLM for path payments

    # reads
    def account(self, address):
        if address != self.pub:
            return None
        return {"sequence": str(self.seq), "subentry_count": 1 + len(self.offers), "balances": [
            {"asset_type": "native", "balance": amt(self.xlm_bal), "selling_liabilities": "0"},
            {"asset_type": "credit_alphanum4", "asset_code": "USDC", "asset_issuer": self.usdc.issuer,
             "balance": amt(self.usdc_bal), "selling_liabilities": "0"}]}

    def order_book(self):
        return {"bids": [{"price": str(self.bid), "amount": "1000"}],
                "asks": [{"price": str(self.ask), "amount": "1000"}]}

    def offer(self, offer_id):
        o = self.offers.get(int(offer_id))
        return dict(o) if o else None

    def offer_trades(self, offer_id):
        return list(self.trades.get(int(offer_id), []))

    def account_offers(self, address):
        return [dict(o) for o in self.offers.values()]

    def strict_send(self, direction, send_amount):
        rate = self.path_rate or (self.ask if direction == "buy" else self.bid)
        a = Decimal(send_amount)
        dest = a / rate if direction == "buy" else a * rate
        return [{"destination_amount": amt(dest), "source_amount": send_amount, "path": []}]

    # writes
    def submit(self, xdr):
        te = TransactionEnvelope.from_xdr(xdr, network_passphrase(self.network))
        Keypair.from_public_key(self.pub).verify(te.hash(), te.signatures[0].signature)  # signed by the bot
        assert te.transaction.source.account_id == self.pub
        self.submitted.append(te)
        self.seq += 1
        h = te.hash_hex()
        if self.fail_next:
            codes, self.fail_next = self.fail_next, None
            return {"ok": False, "hash": h, "codes": codes, "error": "tx_failed"}
        ops = te.transaction.operations
        name = type(ops[0]).__name__
        if name == "PathPaymentStrictSend":
            op = ops[0]
            send = Decimal(op.send_amount)
            buy = op.send_asset.is_native() is False
            rate = self.path_rate or (self.ask if buy else self.bid)
            got = send / rate if buy else send * rate
            if got < Decimal(op.dest_min):
                return {"ok": False, "hash": h, "codes": {"transaction": "tx_failed",
                                                           "operations": ["op_under_dest_min"]}}
            if buy:
                self.usdc_bal -= send; self.xlm_bal += got
            else:
                self.xlm_bal -= send; self.usdc_bal += got
            last = X.SimplePaymentResult(destination=Keypair.from_public_key(self.pub).xdr_account_id(),
                                         asset=(Asset.native() if buy else self.usdc).to_xdr_object(), amount=_amt(amt(got)))
            tr = X.OperationResultTr(X.OperationType.PATH_PAYMENT_STRICT_SEND,
                                     path_payment_strict_send_result=X.PathPaymentStrictSendResult(
                                         X.PathPaymentStrictSendResultCode.PATH_PAYMENT_STRICT_SEND_SUCCESS,
                                         success=X.PathPaymentStrictSendResultSuccess(offers=[], last=last)))
        elif name == "ManageBuyOffer":
            op = ops[0]
            price = Decimal(op.price.n) / Decimal(op.price.d)
            qty = Decimal(op.amount)
            claims = []
            if price >= self.ask:   # crosses: fill fully at the ask
                cost = qty * self.ask
                claims.append(_claim(self.maker, Asset.native(), qty, self.usdc, cost))
                self.xlm_bal += qty; self.usdc_bal -= cost
                effect, entry = X.ManageOfferEffect.MANAGE_OFFER_DELETED, None
            else:
                oid = self.next_offer; self.next_offer += 1
                self.offers[oid] = {"id": oid, "selling": {"asset_type": "credit_alphanum4", "asset_code": "USDC",
                                                           "asset_issuer": self.usdc.issuer},
                                    "buying": {"asset_type": "native"}, "amount": amt(qty * price),
                                    "price": amt(Decimal(1) / price), "_xlm": qty, "_price": price}
                effect = X.ManageOfferEffect.MANAGE_OFFER_CREATED
                entry = X.OfferEntry(seller_id=Keypair.from_public_key(self.pub).xdr_account_id(), offer_id=_i64(oid),
                                     selling=self.usdc.to_xdr_object(), buying=Asset.native().to_xdr_object(),
                                     amount=_amt(amt(qty * price)), price=X.Price(X.Int32(1), X.Int32(1)),
                                     flags=X.Uint32(0), ext=X.OfferEntryExt(0))
            tr = X.OperationResultTr(X.OperationType.MANAGE_BUY_OFFER, manage_buy_offer_result=X.ManageBuyOfferResult(
                X.ManageBuyOfferResultCode.MANAGE_BUY_OFFER_SUCCESS,
                success=X.ManageOfferSuccessResult(offers_claimed=claims,
                                                   offer=X.ManageOfferSuccessResultOffer(effect, entry))))
        elif name == "ManageSellOffer":
            for op in ops:
                assert Decimal(op.amount) == 0, "only cancels are expected through manageSellOffer here"
                self.offers.pop(int(op.offer_id), None)
            tr = X.OperationResultTr(X.OperationType.MANAGE_SELL_OFFER, manage_sell_offer_result=X.ManageSellOfferResult(
                X.ManageSellOfferResultCode.MANAGE_SELL_OFFER_SUCCESS,
                success=X.ManageOfferSuccessResult(offers_claimed=[], offer=X.ManageOfferSuccessResultOffer(
                    X.ManageOfferEffect.MANAGE_OFFER_DELETED))))
        else:  # pragma: no cover
            raise AssertionError(name)
        return {"ok": True, "hash": h, "ledger": 55, "paging_token": str((55 << 32) + 4096),
                "result_xdr": _tx_result(tr), "fee_charged": "100", "successful": True}


# ---------------------------------------------------------------- app helpers
def make(tmp_path, network="testnet", allow_live="0", **env):
    tmp_path.mkdir(parents=True, exist_ok=True)
    dotenv = tmp_path / ".env"
    bot = Keypair.random()
    dotenv.write_text(f"BRIDGE_TOKEN={TOKEN}\n", encoding="utf-8")
    base = {"BRIDGE_TOKEN": TOKEN, "BRIDGE_STATE_DIR": str(tmp_path / "state"),
            "TRADELOCKER_EMAIL": "robert@example.com", "TRADELOCKER_PASSWORD": "x", "TRADELOCKER_SERVER": "demo",
            "STELLAR_NETWORK": network, "ALPHA_HIVE_ALLOW_LIVE": allow_live,
            "STELLAR_BOT_PUBLIC_KEY": bot.public_key, "STELLAR_BOT_SECRET": bot.secret}
    s = load_settings({**base, **env}, dotenv_path=dotenv)
    app = create_app(s)
    c = TestClient(app)
    fake = FakeHorizon("public" if network == "public" else "testnet", bot.public_key)
    app.state.bridge["horizon_api"] = fake
    c.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    return c, {"X-Session-Token": tok}, fake, s, bot


def arm_testnet(c, h):
    r = c.post("/stellar/mode", json={"mode": "TESTNET"}, headers=h)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- tests
def test_parse_result_reads_path_payment_and_offer():
    pub = Keypair.random().public_key
    usdc = Asset("USDC", usdc_issuer("testnet"))
    last = X.SimplePaymentResult(destination=Keypair.from_public_key(pub).xdr_account_id(),
                                 asset=Asset.native().to_xdr_object(), amount=_amt("39.8"))
    tr = X.OperationResultTr(X.OperationType.PATH_PAYMENT_STRICT_SEND,
                             path_payment_strict_send_result=X.PathPaymentStrictSendResult(
                                 X.PathPaymentStrictSendResultCode.PATH_PAYMENT_STRICT_SEND_SUCCESS,
                                 success=X.PathPaymentStrictSendResultSuccess(offers=[], last=last)))
    p = parse_tx_result(_tx_result(tr, fee=100))
    assert p["xlm_in"] == Decimal("39.8") and p["fee_stroops"] == 100
    claim = _claim(pub, Asset.native(), "4", usdc, "1")
    tr2 = X.OperationResultTr(X.OperationType.MANAGE_BUY_OFFER, manage_buy_offer_result=X.ManageBuyOfferResult(
        X.ManageBuyOfferResultCode.MANAGE_BUY_OFFER_SUCCESS,
        success=X.ManageOfferSuccessResult(offers_claimed=[claim], offer=X.ManageOfferSuccessResultOffer(
            X.ManageOfferEffect.MANAGE_OFFER_DELETED))))
    p2 = parse_tx_result(_tx_result(tr2))
    assert p2["xlm_in"] == Decimal("4") and p2["usdc_out"] == Decimal("1") and p2["offer_id"] is None
    assert p2["offer_effect"] == "deleted"


def test_market_buy_submits_signed_path_payment_within_caps(tmp_path):
    c, h, fake, s, bot = make(tmp_path, xlm="500")
    fake.usdc_bal = Decimal("1000")   # 20% of balance would be $200: the $25 cap must bind
    arm_testnet(c, h)
    r = c.post("/stellar/orders", json={**ORDER, "balance": 999999}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["submitted"] is True and body["mode"] == "TESTNET"
    o = body["order"]
    assert o["status"] == "filled" and o["type"] == "market" and o["tx_hash"]
    assert o["tx_link"].startswith("https://stellar.expert/explorer/testnet/tx/")
    assert body["plan"]["notional_usd"] <= 25.0
    assert Decimal(o["send_amount"]) <= Decimal("25")
    assert Decimal(o["dest_min"]) < Decimal(o["dest_quote"])   # slippage floor applied
    assert len(fake.submitted) == 1
    assert bot.secret not in r.text
    lst = c.get("/stellar/orders", headers=h).json()
    assert lst["orders"][0]["id"] == o["id"] and lst["fills"][0]["side"] == "buy"
    assert lst["pnl"]["net_xlm"] > 0 and lst["daily"]["limit_usd"] == 5.0
    assert bot.secret not in json.dumps(lst)
    # audit log never holds the secret
    assert bot.secret not in (s.state_dir / "orders.jsonl").read_text()
    assert bot.secret not in (s.state_dir / "stellar_ledger_testnet.json").read_text()


def test_position_cap_trims_then_blocks_more_buys(tmp_path):
    c, h, fake, s, _ = make(tmp_path)
    fake.usdc_bal = Decimal("1000")
    arm_testnet(c, h)
    total = Decimal(0)
    for _ in range(3):
        r = c.post("/stellar/orders", json=ORDER, headers=h)
        if r.status_code != 200:
            break
        total += Decimal(r.json()["order"]["send_amount"])
    assert total <= Decimal("25.01")
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "max_position"


def test_sell_is_spot_only(tmp_path):
    c, h, fake, s, _ = make(tmp_path)
    fake.xlm_bal = Decimal("2.5")   # only reserve + fee buffer: nothing spare to sell
    arm_testnet(c, h)
    r = c.post("/stellar/orders", json={**ORDER, "side": "sell", "stop": 0.26, "target": 0.23,
                                        "queen": {"call": "SHORT"}}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "spot_only"
    assert fake.submitted == []


def test_risk_and_queen_gates_never_reach_horizon(tmp_path):
    c, h, fake, _, _ = make(tmp_path)
    arm_testnet(c, h)
    assert c.post("/stellar/orders", json={**ORDER, "risk": {"status": "reject"}}, headers=h).status_code == 403
    assert c.post("/stellar/orders", json={**ORDER, "queen": {"call": "SHORT"}}, headers=h).status_code == 403
    assert c.post("/stellar/orders", json={**ORDER, "symbol": "BTC-USD"}, headers=h).status_code == 400
    assert fake.submitted == []


def test_unarmed_testnet_is_preview_only(tmp_path):
    c, h, fake, _, _ = make(tmp_path)
    r = c.post("/stellar/orders", json={**ORDER, "entry": 0.25, "balance": 100}, headers=h)
    assert r.status_code == 200 and r.json()["preview"] is True and r.json()["submitted"] is False
    assert fake.submitted == []


def test_limit_order_rests_then_cancel(tmp_path):
    c, h, fake, _, _ = make(tmp_path)
    arm_testnet(c, h)
    r = c.post("/stellar/orders", json={**ORDER, "order_type": "limit", "limit_price": 0.2}, headers=h)
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    assert o["status"] == "open" and o["offer_id"] == 7000
    lst = c.get("/stellar/orders", headers=h).json()
    assert lst["open_offers"][0]["id"] == 7000
    # one resting offer is the max: a second order is blocked
    r2 = c.post("/stellar/orders", json=ORDER, headers=h)
    assert r2.status_code == 409 and r2.json()["detail"]["blocked"] == "max_open_positions"
    cx = c.post(f"/stellar/orders/{o['id']}/cancel", headers=h)
    assert cx.status_code == 200 and cx.json()["cancelled"] == [7000] and cx.json()["tx_hash"]
    lst = c.get("/stellar/orders", headers=h).json()
    assert lst["open_offers"] == [] and lst["orders"][0]["status"] == "cancelled"


def test_marketable_limit_fills_immediately(tmp_path):
    c, h, fake, _, _ = make(tmp_path)
    arm_testnet(c, h)
    r = c.post("/stellar/orders", json={**ORDER, "order_type": "limit", "limit_price": 0.26}, headers=h)
    o = r.json()["order"]
    assert o["status"] == "filled" and Decimal(o["filled_xlm"]) > 0


def test_resting_offer_later_fill_is_recorded(tmp_path):
    c, h, fake, _, _ = make(tmp_path)
    arm_testnet(c, h)
    o = c.post("/stellar/orders", json={**ORDER, "order_type": "limit", "limit_price": 0.2}, headers=h).json()["order"]
    # someone takes the whole offer later
    fake.trades[7000] = [{"id": "999999-0", "base_asset_type": "native", "base_amount": o["xlm"],
                          "counter_asset_type": "credit_alphanum4", "counter_amount": amt(Decimal(o["xlm"]) * Decimal("0.2")),
                          "ledger_close_time": "2026-11-27T20:00:00Z"}]
    fake.offers.pop(7000)
    lst = c.get("/stellar/orders", headers=h).json()
    rec = next(x for x in lst["orders"] if x["id"] == o["id"])
    assert rec["status"] == "filled" and Decimal(rec["filled_xlm"]) == Decimal(o["xlm"])


def test_kill_switch_cancels_offers_disarms_and_blocks(tmp_path):
    c, h, fake, s, _ = make(tmp_path)
    arm_testnet(c, h)
    c.post("/stellar/orders", json={**ORDER, "order_type": "limit", "limit_price": 0.2}, headers=h)
    assert fake.offers
    k = c.post("/stellar/kill", json={"reason": "test"}, headers=h)
    assert k.status_code == 200
    kb = k.json()
    assert kb["kill_switch"] is True and kb["disarmed"] is True and kb["cancel"]["cancelled"] == [7000]
    assert fake.offers == {}
    sess = c.get("/session", headers=h).json()
    assert sess["stellar_testnet_armed"] is False
    # cannot re-arm while the kill switch is latched
    assert c.post("/stellar/mode", json={"mode": "TESTNET"}, headers=h).status_code == 423
    n = len(fake.submitted)
    r = c.post("/stellar/orders", json=ORDER, headers=h)
    assert r.status_code in (403, 423)
    assert len(fake.submitted) == n


def test_daily_loss_stop_latches_and_cancels_offers(tmp_path):
    c, h, fake, s, _ = make(tmp_path)
    arm_testnet(c, h)
    c.post("/stellar/orders", json={**ORDER, "order_type": "limit", "limit_price": 0.2}, headers=h)
    # Record a bad fill today: bought 100 XLM at 0.31; mid is 0.25 -> -$6 marked to market
    led = Ledger(s.state_dir, "testnet")
    led.add_fill(make_fill("x", "manual:1", "buy", "100", "31"))
    r = c.post("/stellar/orders", json=ORDER, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "daily_loss_stop"
    assert fake.offers == {}   # resting offer cancelled when the stop hit
    snap = c.get("/stellar/orders", headers=h).json()
    assert snap["daily"]["stopped"] is True and snap["daily"]["loss_usd"] >= 5


def test_logout_disarms_so_orders_become_preview(tmp_path):
    c, h, fake, _, _ = make(tmp_path)
    arm_testnet(c, h)
    c.post("/logout", headers=h)
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h2 = {"X-Session-Token": tok}
    r = c.post("/stellar/orders", json={**ORDER, "entry": 0.25, "balance": 100}, headers=h2)
    assert r.status_code == 200 and r.json()["preview"] is True
    assert fake.submitted == []


def test_failed_submission_is_recorded_not_filled(tmp_path):
    c, h, fake, _, _ = make(tmp_path)
    arm_testnet(c, h)
    fake.fail_next = {"transaction": "tx_failed", "operations": ["op_under_dest_min"]}
    r = c.post("/stellar/orders", json=ORDER, headers=h)
    assert r.status_code == 200
    o = r.json()["order"]
    assert r.json()["submitted"] is False and o["status"] == "failed" and "op_under_dest_min" in o["error"]
    assert c.get("/stellar/orders", headers=h).json()["fills"] == []


def test_price_impact_guard(tmp_path):
    c, h, fake, _, _ = make(tmp_path)
    arm_testnet(c, h)
    fake.path_rate = Decimal("0.30")   # path quote 20% above the ask
    r = c.post("/stellar/orders", json=ORDER, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["blocked"] == "price_impact"
    assert fake.submitted == []


def test_mainnet_gate(tmp_path):
    # 1. Flag off: arm refused, orders preview only, nothing submitted.
    c, h, fake, _, _ = make(tmp_path / "a", network="public", allow_live="0")
    assert c.post("/stellar/mode", json={"mode": "LIVE", "confirm": LIVE_CONFIRM_PHRASE}, headers=h).status_code == 403
    assert c.post("/stellar/mode", json={"mode": "TESTNET"}, headers=h).status_code == 400
    r = c.post("/stellar/orders", json={**ORDER, "entry": 0.25, "balance": 100}, headers=h)
    assert r.json()["preview"] is True and fake.submitted == []
    assert c.post("/stellar/cancel-all", headers=h).status_code == 403
    # 2. Flag on but not armed: still preview.
    c, h, fake, _, _ = make(tmp_path / "b", network="public", allow_live="1")
    r = c.post("/stellar/orders", json={**ORDER, "entry": 0.25, "balance": 100}, headers=h)
    assert r.json()["preview"] is True and fake.submitted == []
    # 3. CLI token cannot arm mainnet; wrong phrase cannot arm.
    assert c.post("/stellar/mode", json={"mode": "LIVE", "confirm": LIVE_CONFIRM_PHRASE},
                  headers={"X-Bridge-Token": TOKEN}).status_code == 403
    assert c.post("/stellar/mode", json={"mode": "LIVE", "confirm": "yes"}, headers=h).status_code == 400
    # 4. Session + flag + phrase arms; the FAKE Horizon receives a mainnet-signed envelope.
    assert c.post("/stellar/mode", json={"mode": "LIVE", "confirm": LIVE_CONFIRM_PHRASE}, headers=h).status_code == 200
    r = c.post("/stellar/orders", json=ORDER, headers=h)
    assert r.status_code == 200 and r.json()["mode"] == "LIVE" and len(fake.submitted) == 1
    assert r.json()["plan"]["notional_usd"] <= 25


def test_desk_refuses_mainnet_without_live_ok(tmp_path):
    bot = Keypair.random()
    fake = FakeHorizon("public", bot.public_key)
    desk = Desk("public", bot.public_key, fake, Ledger(tmp_path, "public"))
    with pytest.raises(OrderError) as e:
        desk.place(side="buy", units=Decimal("10"), order_type="market", entry=Decimal("0.25"), slippage_pct=0.5,
                   limit_price=None, secret=bot.secret, live_ok=False, meta={})
    assert e.value.code == "mainnet_locked" and fake.submitted == []


def test_pnl_summary_marks_to_market():
    data = {"fills": [make_fill("a", "k1", "buy", "100", "25"), make_fill("a", "k2", "sell", "40", "11")]}
    p = pnl_summary(data, Decimal("0.26"), day=utc_day())
    # buy 100 @0.25 -> +1.00 at 0.26; sell 40 @0.275 -> +0.60 vs 0.26
    assert p["net_xlm"] == 60 and abs(p["today_pnl_usd"] - 1.6) < 1e-6
