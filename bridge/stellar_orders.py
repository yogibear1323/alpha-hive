"""Live session order desk: XLM/USDC on the Stellar DEX, signed by the dedicated bot account.

Builds, signs and submits:
  * market-style orders as a pathPaymentStrictSend to the bot's own address with a
    floored minimum received (slippage limit), and a price-impact check against the book;
  * limit orders as manageBuyOffer (buy XLM with USDC) or manageSellOffer (sell XLM for USDC);
  * cancels as manageSellOffer amount 0 for each open offer.

Every submission goes through `Desk.submit`, which refuses mainnet unless the caller
passes `live_ok=True` (ALPHA_HIVE_ALLOW_LIVE=1 + typed arm + unlocked session, checked
by the route). The bot secret is only ever passed to `sign_xdr`; it is never stored on
the desk, returned, logged, or written to the ledger file.

Ledger: bridge/state/stellar_ledger_<network>.json holds orders and fills (no secrets).
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path

import httpx

from bridge.stellar_dex import (
    horizon_url, is_secret_key, network_passphrase, normalize_network, sign_xdr, usdc_issuer,
)

D7 = Decimal("0.0000001")
EXPLORER = {"testnet": "https://stellar.expert/explorer/testnet/tx/",
            "public": "https://stellar.expert/explorer/public/tx/"}
MAX_SLIPPAGE_PCT = 2.0
MAX_PRICE_IMPACT_PCT = 2.0
BASE_FEE = 100
XLM_FEE_BUFFER = Decimal("1")


class OrderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def dec(v, default=None):
    try:
        if v is None or v == "":
            return default
        d = Decimal(str(v))
        return d if d.is_finite() else default
    except (InvalidOperation, ValueError):
        return default


def amt(d) -> str:
    """7-decimal amount string, floored. Never rounds up."""
    q = Decimal(str(d)).quantize(D7, rounding=ROUND_DOWN)
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def utc_day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%d")


def tx_link(network: str, tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    return EXPLORER[normalize_network(network)] + tx_hash


# ---------------------------------------------------------------- Horizon access
class Horizon:
    """Thin Horizon client. Tests inject a fake with the same methods."""

    def __init__(self, network: str, http: httpx.Client | None = None, timeout: float = 15.0):
        self.network = normalize_network(network)
        self.base = horizon_url(self.network)
        self._http = http
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        http = self._http or httpx.Client(timeout=self.timeout)
        try:
            r = http.get(self.base + path, params=params or {}, headers={"Accept": "application/json"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        finally:
            if self._http is None:
                http.close()

    def account(self, address: str) -> dict | None:
        return self._get(f"/accounts/{address}")

    def order_book(self) -> dict:
        body = self._get("/order_book", {
            "selling_asset_type": "native", "buying_asset_type": "credit_alphanum4",
            "buying_asset_code": "USDC", "buying_asset_issuer": usdc_issuer(self.network), "limit": 5}) or {}
        return body

    def offer(self, offer_id) -> dict | None:
        return self._get(f"/offers/{int(offer_id)}")

    def offer_trades(self, offer_id) -> list:
        body = self._get(f"/offers/{int(offer_id)}/trades", {"limit": 100, "order": "asc"}) or {}
        return ((body.get("_embedded") or {}).get("records")) or []

    def account_offers(self, address: str) -> list:
        body = self._get(f"/accounts/{address}/offers", {"limit": 100}) or {}
        return ((body.get("_embedded") or {}).get("records")) or []

    def strict_send(self, direction: str, send_amount: str) -> list:
        issuer = usdc_issuer(self.network)
        if direction == "buy":   # USDC -> XLM
            params = {"source_asset_type": "credit_alphanum4", "source_asset_code": "USDC",
                      "source_asset_issuer": issuer, "source_amount": send_amount, "destination_assets": "native"}
        else:                    # XLM -> USDC
            params = {"source_asset_type": "native", "source_amount": send_amount,
                      "destination_assets": f"USDC:{issuer}"}
        body = self._get("/paths/strict-send", params) or {}
        return ((body.get("_embedded") or {}).get("records")) or []

    def submit(self, xdr: str) -> dict:
        from stellar_sdk import Server
        from stellar_sdk.exceptions import BadRequestError, BaseHorizonError
        server = Server(self.base)
        try:
            res = server.submit_transaction(xdr)
            return {"ok": True, **_pick_tx(res)}
        except BadRequestError as e:
            extras = (getattr(e, "extras", None) or {})
            return {"ok": False, "codes": extras.get("result_codes") or {}, "hash": extras.get("hash"),
                    "error": "tx_failed"}
        except BaseHorizonError as e:  # pragma: no cover - network edge
            return {"ok": False, "codes": {}, "error": f"horizon_{getattr(e, 'status', '')}"}


def _pick_tx(res: dict) -> dict:
    return {"hash": res.get("hash"), "ledger": res.get("ledger"), "paging_token": res.get("paging_token"),
            "result_xdr": res.get("result_xdr"), "fee_charged": res.get("fee_charged"),
            "successful": res.get("successful", True)}


# ---------------------------------------------------------------- book helpers
def best_prices(book: dict) -> dict:
    """Book for selling=XLM / buying=USDC: prices are USDC per XLM."""
    bids = (book or {}).get("bids") or []
    asks = (book or {}).get("asks") or []
    bid = dec(bids[0].get("price")) if bids else None
    ask = dec(asks[0].get("price")) if asks else None
    mid = (bid + ask) / 2 if bid and ask else (bid or ask)
    return {"bid": bid, "ask": ask, "mid": mid}


def balances(account: dict, network: str) -> dict:
    issuer = usdc_issuer(network)
    xlm = usdc = Decimal(0)
    xlm_sell_liab = usdc_sell_liab = Decimal(0)
    has_trust = False
    for b in (account or {}).get("balances") or []:
        if b.get("asset_type") == "native":
            xlm = dec(b.get("balance"), Decimal(0))
            xlm_sell_liab = dec(b.get("selling_liabilities"), Decimal(0))
        elif b.get("asset_code") == "USDC" and b.get("asset_issuer") == issuer:
            usdc = dec(b.get("balance"), Decimal(0))
            usdc_sell_liab = dec(b.get("selling_liabilities"), Decimal(0))
            has_trust = True
    subentries = int((account or {}).get("subentry_count") or 0)
    reserve = (Decimal(2) + subentries) * Decimal("0.5")
    xlm_spendable = max(Decimal(0), xlm - reserve - xlm_sell_liab - XLM_FEE_BUFFER)
    usdc_spendable = max(Decimal(0), usdc - usdc_sell_liab)
    return {"xlm": xlm, "usdc": usdc, "xlm_spendable": xlm_spendable, "usdc_spendable": usdc_spendable,
            "has_usdc_trustline": has_trust, "reserve_xlm": reserve}


# ---------------------------------------------------------------- tx result parsing
def parse_tx_result(result_xdr: str | None) -> dict:
    """Read fills and the created offer id from a successful single-op transaction result.

    Returns xlm_in/xlm_out/usdc_in/usdc_out from the bot's point of view, the offer id
    if an offer is left resting, and fee_charged in stroops.
    """
    out = {"offer_id": None, "offer_effect": None, "xlm_in": Decimal(0), "xlm_out": Decimal(0),
           "usdc_in": Decimal(0), "usdc_out": Decimal(0), "claims": 0, "fee_stroops": 0}
    if not result_xdr:
        return out
    from stellar_sdk import xdr as X
    tr = X.TransactionResult.from_xdr(result_xdr)
    out["fee_stroops"] = int(tr.fee_charged.int64)
    results = tr.result.results or []
    if not results:
        return out
    op = results[0].tr
    t = op.type

    def _is_native(a) -> bool:
        return a.type == X.AssetType.ASSET_TYPE_NATIVE

    def _claim(atom):
        body = atom.order_book or atom.v0 or atom.liquidity_pool
        # Counterparty sold `asset_sold` to us and received `asset_bought` from us.
        got = Decimal(body.amount_sold.int64) / Decimal(10**7)
        gave = Decimal(body.amount_bought.int64) / Decimal(10**7)
        if _is_native(body.asset_sold):
            out["xlm_in"] += got
            out["usdc_out"] += gave
        else:
            out["usdc_in"] += got
            out["xlm_out"] += gave
        out["claims"] += 1

    if t == X.OperationType.MANAGE_BUY_OFFER or t == X.OperationType.MANAGE_SELL_OFFER:
        res = op.manage_buy_offer_result if t == X.OperationType.MANAGE_BUY_OFFER else op.manage_sell_offer_result
        succ = res.success
        for atom in succ.offers_claimed or []:
            _claim(atom)
        eff = succ.offer.effect
        out["offer_effect"] = {0: "created", 1: "updated", 2: "deleted"}.get(int(eff.value), str(eff))
        if succ.offer.offer is not None:
            out["offer_id"] = int(succ.offer.offer.offer_id.int64)
    elif t == X.OperationType.PATH_PAYMENT_STRICT_SEND:
        succ = op.path_payment_strict_send_result.success
        last = succ.last
        got = Decimal(last.amount.int64) / Decimal(10**7)
        if _is_native(last.asset):
            out["xlm_in"] = got
        else:
            out["usdc_in"] = got
        out["claims"] = len(succ.offers or [])
    return out


# ---------------------------------------------------------------- ledger
class Ledger:
    def __init__(self, state_dir: Path, network: str, clock=time.time):
        self.network = normalize_network(network)
        self.path = Path(state_dir) / f"stellar_ledger_{self.network}.json"
        self.clock = clock

    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        d.setdefault("orders", [])
        d.setdefault("fills", [])
        return d

    def save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add_order(self, order: dict) -> dict:
        d = self.load()
        d["orders"].append(order)
        self.save(d)
        return order

    def update_order(self, order_id: str, **changes) -> dict | None:
        d = self.load()
        for o in d["orders"]:
            if o["id"] == order_id:
                o.update(changes)
                self.save(d)
                return o
        return None

    def add_fill(self, fill: dict) -> bool:
        d = self.load()
        if any(f.get("key") == fill["key"] for f in d["fills"]):
            return False
        d["fills"].append(fill)
        for o in d["orders"]:
            if o["id"] == fill.get("order_id"):
                o["filled_xlm"] = amt(dec(o.get("filled_xlm"), Decimal(0)) + dec(fill["xlm"]))
                o["filled_usdc"] = amt(dec(o.get("filled_usdc"), Decimal(0)) + dec(fill["usdc"]))
        self.save(d)
        return True


def make_fill(order_id: str, key: str, side: str, xlm, usdc, fee_stroops: int = 0, ts: float | None = None) -> dict:
    ts = ts if ts is not None else time.time()
    xlm, usdc = dec(xlm, Decimal(0)), dec(usdc, Decimal(0))
    price = (usdc / xlm) if xlm > 0 else Decimal(0)
    return {"key": key, "order_id": order_id, "ts": round(ts, 3), "day": utc_day(ts), "side": side,
            "xlm": amt(xlm), "usdc": amt(usdc), "price": amt(price),
            "fee_xlm": amt(Decimal(int(fee_stroops or 0)) / Decimal(10**7))}


def pnl_summary(ledger_data: dict, mid: Decimal | None, day: str | None = None) -> dict:
    """Mark-to-market P&L from recorded fills (buys and sells of XLM against USDC), fees included."""
    day = day or utc_day()
    m = mid or Decimal(0)
    net_xlm = Decimal(0)
    cash = Decimal(0)
    today = Decimal(0)
    fees_usd = Decimal(0)
    for f in ledger_data.get("fills") or []:
        x, u, fee = dec(f.get("xlm"), Decimal(0)), dec(f.get("usdc"), Decimal(0)), dec(f.get("fee_xlm"), Decimal(0))
        if f.get("side") == "buy":
            net_xlm += x
            cash -= u
            leg = x * m - u
        else:
            net_xlm -= x
            cash += u
            leg = u - x * m
        leg -= fee * m
        cash -= fee * m
        fees_usd += fee * m
        if f.get("day") == day:
            today += leg
    total = cash + net_xlm * m
    return {"net_xlm": float(round(net_xlm, 7)), "position_usd": float(round(net_xlm * m, 4)),
            "today_pnl_usd": float(round(today, 4)), "total_pnl_usd": float(round(total, 4)),
            "fees_usd": float(round(fees_usd, 4)), "mid": float(m) if m else None, "day": day}


# ---------------------------------------------------------------- transaction builders
def _assets(network: str):
    from stellar_sdk import Asset
    return Asset.native(), Asset("USDC", usdc_issuer(network))


def _builder(network: str, public: str, sequence):
    from stellar_sdk import Account, TransactionBuilder
    return TransactionBuilder(Account(public, int(sequence)), network_passphrase(network), base_fee=BASE_FEE)


def build_market_xdr(network, public, sequence, side, send_amount, dest_min, path=None) -> str:
    from stellar_sdk import Asset
    xlm, usdc = _assets(network)
    send_asset, dest_asset = (usdc, xlm) if side == "buy" else (xlm, usdc)
    hops = []
    for p in path or []:
        hops.append(Asset.native() if p.get("asset_type") == "native" else Asset(p["asset_code"], p["asset_issuer"]))
    b = _builder(network, public, sequence)
    b.append_path_payment_strict_send_op(destination=public, send_asset=send_asset, send_amount=amt(send_amount),
                                         dest_asset=dest_asset, dest_min=amt(dest_min), path=hops)
    return b.set_timeout(60).build().to_xdr()


def build_limit_xdr(network, public, sequence, side, xlm_amount, price) -> str:
    xlm, usdc = _assets(network)
    b = _builder(network, public, sequence)
    if side == "buy":
        # Buy `xlm_amount` XLM paying at most `price` USDC per XLM.
        b.append_manage_buy_offer_op(selling=usdc, buying=xlm, amount=amt(xlm_amount), price=amt(price))
    else:
        b.append_manage_sell_offer_op(selling=xlm, buying=usdc, amount=amt(xlm_amount), price=amt(price))
    return b.set_timeout(60).build().to_xdr()


def build_cancel_xdr(network, public, sequence, offers: list) -> str:
    """Cancel each Horizon offer record (manageSellOffer amount 0, same pair and price)."""
    from stellar_sdk import Asset
    b = _builder(network, public, sequence)

    def _a(rec):
        return Asset.native() if rec.get("asset_type") == "native" else Asset(rec["asset_code"], rec["asset_issuer"])

    for o in offers[:100]:
        b.append_manage_sell_offer_op(selling=_a(o["selling"]), buying=_a(o["buying"]), amount="0",
                                      price=o.get("price") or "1", offer_id=int(o["id"]))
    return b.set_timeout(60).build().to_xdr()


# ---------------------------------------------------------------- desk
class Desk:
    def __init__(self, network: str, public: str, horizon, ledger: Ledger, audit=None):
        self.network = normalize_network(network)
        self.public = public
        self.h = horizon
        self.ledger = ledger
        self.audit = audit or (lambda e: None)

    # -- the one submission chokepoint --
    def submit(self, xdr_unsigned: str, secret: str, live_ok: bool) -> dict:
        if self.network == "public" and not live_ok:
            raise OrderError("mainnet_locked", "Mainnet submission refused: needs ALPHA_HIVE_ALLOW_LIVE=1, a restart, "
                                               "an unlocked session and the typed LIVE arm.")
        if not live_ok:
            raise OrderError("not_armed", "Live session is not armed.")
        if not is_secret_key(secret):
            raise OrderError("no_bot_account", "No dedicated bot account is configured on this bridge.")
        signed = sign_xdr(xdr_unsigned, secret, self.network)
        return self.h.submit(signed)

    def account(self) -> dict:
        acc = self.h.account(self.public)
        if not acc:
            raise OrderError("not_funded", "The bot account is not on-chain yet. Fund it with XLM first.")
        return acc

    def prices(self) -> dict:
        return best_prices(self.h.order_book())

    def equity(self, acc: dict, mid) -> Decimal:
        b = balances(acc, self.network)
        return b["usdc"] + b["xlm"] * (mid or Decimal(0))

    def open_orders(self) -> list:
        return [o for o in self.ledger.load()["orders"] if o.get("status") in ("open", "partial")]

    # -- placing --
    def place(self, *, side: str, units: Decimal, order_type: str, entry: Decimal, slippage_pct: float,
              limit_price: Decimal | None, secret: str, live_ok: bool, meta: dict) -> dict:
        acc = self.account()
        seq = acc["sequence"]
        order_id = uuid.uuid4().hex[:12]
        rec = {"id": order_id, "ts": round(time.time(), 3), "network": self.network, "side": side,
               "type": order_type, "status": "submitting", "xlm": amt(units), "price": None,
               "tx_hash": None, "tx_link": None, "offer_id": None, "filled_xlm": "0", "filled_usdc": "0",
               "error": None, **{k: v for k, v in meta.items() if k in ("client_ref", "queen", "risk", "plan")}}
        slip = Decimal(str(slippage_pct))
        if order_type == "market":
            if side == "buy":
                send = (units * entry).quantize(D7, rounding=ROUND_DOWN)
                paths = self.h.strict_send("buy", amt(send))
            else:
                send = units.quantize(D7, rounding=ROUND_DOWN)
                paths = self.h.strict_send("sell", amt(send))
            best = None
            for p in paths:
                if best is None or dec(p.get("destination_amount"), Decimal(0)) > dec(best.get("destination_amount"), Decimal(0)):
                    best = p
            if not best:
                raise OrderError("no_path", "No XLM/USDC path on the DEX right now.")
            dest = dec(best.get("destination_amount"), Decimal(0))
            if dest <= 0:
                raise OrderError("no_path", "The DEX quote was empty.")
            implied = (send / dest) if side == "buy" else (dest / send)   # USDC per XLM
            impact = Decimal(str(MAX_PRICE_IMPACT_PCT)) / 100
            if side == "buy" and entry and implied > entry * (1 + impact):
                raise OrderError("price_impact", f"Fill price {amt(implied)} is more than {MAX_PRICE_IMPACT_PCT}% above the book ask {amt(entry)}.")
            if side == "sell" and entry and implied < entry * (1 - impact):
                raise OrderError("price_impact", f"Fill price {amt(implied)} is more than {MAX_PRICE_IMPACT_PCT}% below the book bid {amt(entry)}.")
            dest_min = (dest * (1 - slip / 100)).quantize(D7, rounding=ROUND_DOWN)
            xdr = build_market_xdr(self.network, self.public, seq, side, send, dest_min, best.get("path") or [])
            rec.update(price=amt(implied), send_amount=amt(send), dest_quote=amt(dest), dest_min=amt(dest_min),
                       slippage_pct=float(slip))
        else:
            price = limit_price or entry
            if not price or price <= 0:
                raise OrderError("bad_price", "A limit order needs a price.")
            xdr = build_limit_xdr(self.network, self.public, seq, side, units, price)
            rec.update(price=amt(price))
        res = self.submit(xdr, secret, live_ok)
        rec["tx_hash"] = res.get("hash")
        rec["tx_link"] = tx_link(self.network, res.get("hash"))
        if not res.get("ok"):
            rec.update(status="failed", error=_codes_text(res))
            self.ledger.add_order(rec)
            self.audit({"result": "stellar_order_failed", "order_id": order_id, "side": side, "type": order_type,
                        "tx_hash": res.get("hash"), "codes": res.get("codes")})
            return rec
        parsed = parse_tx_result(res.get("result_xdr"))
        rec["op_toid"] = _op_toid(res.get("paging_token"))
        self.ledger.add_order(rec)
        fee = parsed["fee_stroops"]
        if order_type == "market":
            if side == "buy":
                xlm_got, usdc_paid = parsed["xlm_in"], dec(rec["send_amount"])
            else:
                xlm_got, usdc_paid = dec(rec["send_amount"]), parsed["usdc_in"]
            self.ledger.add_fill(make_fill(order_id, f"{res.get('hash')}:0", side, xlm_got, usdc_paid, fee))
            status = "filled"
        else:
            if parsed["claims"]:
                xlm_amt = parsed["xlm_in"] if side == "buy" else parsed["xlm_out"]
                usdc_amt = parsed["usdc_out"] if side == "buy" else parsed["usdc_in"]
                self.ledger.add_fill(make_fill(order_id, f"{res.get('hash')}:0", side, xlm_amt, usdc_amt, fee))
            if parsed["offer_id"] and parsed["offer_effect"] != "deleted":
                status = "partial" if parsed["claims"] else "open"
            else:
                status = "filled" if parsed["claims"] else "closed"
        rec = self.ledger.update_order(order_id, status=status, offer_id=parsed["offer_id"],
                                       fee_xlm=amt(Decimal(fee) / Decimal(10**7))) or rec
        self.audit({"result": "stellar_order_submitted", "order_id": order_id, "side": side, "type": order_type,
                    "status": status, "tx_hash": res.get("hash"), "offer_id": parsed["offer_id"]})
        return rec

    # -- status --
    def refresh(self) -> list:
        """Poll open offers: record later fills, mark filled/closed when the offer is gone."""
        for o in self.open_orders():
            oid = o.get("offer_id")
            if not oid:
                continue
            for t in self.h.offer_trades(oid):
                if _trade_op(t) and o.get("op_toid") and _trade_op(t) == o["op_toid"]:
                    continue   # already counted from the submission result
                xlm_a, usdc_a = _trade_amounts(t)
                if xlm_a <= 0:
                    continue
                self.ledger.add_fill(make_fill(o["id"], f"trade:{t.get('id')}", o["side"], xlm_a, usdc_a, 0,
                                               ts=_ts(t.get("ledger_close_time"))))
            live = self.h.offer(oid)
            cur = next((x for x in self.ledger.load()["orders"] if x["id"] == o["id"]), o)
            filled = dec(cur.get("filled_xlm"), Decimal(0))
            if live:
                self.ledger.update_order(o["id"], status="partial" if filled > 0 else "open",
                                         remaining=live.get("amount"))
            else:
                self.ledger.update_order(o["id"], status="filled" if filled > 0 else "closed", remaining="0")
        return self.ledger.load()["orders"]

    # -- cancel --
    def cancel(self, *, secret: str, live_ok: bool, offer_ids: list | None = None, reason: str = "") -> dict:
        """Cancel the bot's open offers (all, or the given ids). Uses Horizon's offer list as truth."""
        offers = self.h.account_offers(self.public)
        if offer_ids is not None:
            want = {int(x) for x in offer_ids}
            offers = [o for o in offers if int(o["id"]) in want]
        if not offers:
            self._mark_cancelled(offer_ids)
            return {"ok": True, "cancelled": [], "tx_hash": None, "tx_link": None}
        acc = self.account()
        xdr = build_cancel_xdr(self.network, self.public, acc["sequence"], offers)
        res = self.submit(xdr, secret, live_ok)
        ids = [int(o["id"]) for o in offers]
        if not res.get("ok"):
            self.audit({"result": "stellar_cancel_failed", "offer_ids": ids, "codes": res.get("codes")})
            return {"ok": False, "cancelled": [], "error": _codes_text(res), "tx_hash": res.get("hash"),
                    "tx_link": tx_link(self.network, res.get("hash"))}
        self.refresh_cancelled(ids, res.get("hash"))
        self.audit({"result": "stellar_cancelled", "offer_ids": ids, "tx_hash": res.get("hash"),
                    "reason": reason[:120]})
        return {"ok": True, "cancelled": ids, "tx_hash": res.get("hash"), "tx_link": tx_link(self.network, res.get("hash"))}

    def refresh_cancelled(self, ids: list, tx_hash: str | None):
        # pick up any fills that landed before the cancel, then mark cancelled
        self.refresh()
        for o in self.ledger.load()["orders"]:
            if o.get("offer_id") and int(o["offer_id"]) in set(ids):
                self.ledger.update_order(o["id"], status="cancelled", cancel_tx=tx_hash,
                                         cancel_link=tx_link(self.network, tx_hash))

    def _mark_cancelled(self, offer_ids):
        for o in self.open_orders():
            if offer_ids is None or (o.get("offer_id") and int(o["offer_id"]) in {int(x) for x in offer_ids}):
                self.ledger.update_order(o["id"], status="closed")


def _codes_text(res: dict) -> str:
    c = res.get("codes") or {}
    if isinstance(c, dict) and c:
        ops = c.get("operations") or []
        return f"{c.get('transaction', 'tx_failed')}{(' / ' + ', '.join(ops)) if ops else ''}"
    return str(res.get("error") or "submission failed")


def _op_toid(paging_token) -> str | None:
    try:
        return str(int(paging_token) + 1)  # first (only) operation in the tx
    except (TypeError, ValueError):
        return None


def _trade_op(t: dict) -> str | None:
    tid = str(t.get("id") or "")
    return tid.split("-")[0] if "-" in tid else None


def _trade_amounts(t: dict):
    """XLM and USDC amounts of a Horizon trade record for the XLM/USDC pair."""
    if t.get("base_asset_type") == "native":
        return dec(t.get("base_amount"), Decimal(0)), dec(t.get("counter_amount"), Decimal(0))
    if t.get("counter_asset_type") == "native":
        return dec(t.get("counter_amount"), Decimal(0)), dec(t.get("base_amount"), Decimal(0))
    return Decimal(0), Decimal(0)


def _ts(iso: str | None) -> float:
    if not iso:
        return time.time()
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()
