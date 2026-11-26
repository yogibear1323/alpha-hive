"""Coinbase Advanced Trade adapter, used only by the bridge.

Auth is a Coinbase Developer Platform (CDP) secret API key, signed by the official
``coinbase-advanced-py`` SDK (JWT, ES256 or EdDSA). The wrapper talks to the SDK
and never logs the key name or private key.

Coinbase spot is long-only: SHORT/SELL entry calls are refused (no margin or perps
by default). Native attached TP/SL uses ``attached_order_configuration.trigger_bracket_gtc``
(take-profit ``limit_price``, stop ``stop_trigger_price``). PREVIEW mode calls
``preview_order`` only; ``create_order`` is used solely when the server has armed LIVE.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal, ROUND_DOWN, InvalidOperation

STRATEGY_TAG = "alpha-hive"
CASH = {"USD", "USDC", "USDT", "DAI"}
WATCH = ("BTC-USD", "ETH-USD", "XLM-USD", "SOL-USD")
DEFAULT_PRODUCTS = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XLM": "XLM-USD",
    "XRP": "XRP-USD", "DOGE": "DOGE-USD", "ADA": "ADA-USD", "AVAX": "AVAX-USD",
    "LINK": "LINK-USD", "BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD",
    "SOL-USD": "SOL-USD", "XLM-USD": "XLM-USD",
}


class CoinbaseError(RuntimeError):
    pass


def unescape_private_key(raw: str) -> str:
    """Turn a .env value into a PEM or base64 secret. Strips wrapping quotes and \\n."""
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1]
    return text.replace("\\n", "\n").strip()


def _as_dict(obj):
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj[key] if key in obj else default
    return getattr(obj, key, default)


def _amt(bal):
    if isinstance(bal, (int, float)) and not isinstance(bal, bool):
        return float(bal)
    d = _as_dict(bal) if not isinstance(bal, str) else {"value": bal}
    v = d.get("value", d.get("amount"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dec(v) -> Decimal | None:
    try:
        d = Decimal(str(v))
        return d if d.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def floor_to_increment(value, increment) -> Decimal:
    inc, v = _dec(increment), _dec(value)
    if inc is None or inc <= 0 or v is None:
        raise CoinbaseError("size or increment is not a number")
    return (v / inc).to_integral_value(rounding=ROUND_DOWN) * inc


def fmt_dec(value) -> str:
    d = _dec(value)
    if d is None:
        raise CoinbaseError("not a number")
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def norm_product(name: str) -> str:
    raw = str(name or "").strip().upper().replace("/", "-").replace("_", "-")
    if "-" in raw:
        return raw
    compact = re.sub(r"[^A-Z0-9]", "", raw)
    for quote in ("USDT", "USDC", "USD", "EUR", "GBP"):
        if compact.endswith(quote) and len(compact) > len(quote):
            return f"{compact[:-len(quote)]}-{quote}"
    return compact


def attached_bracket(stop, target) -> dict:
    """Native attached TP/SL. Size is omitted: it matches the parent order."""
    return {"trigger_bracket_gtc": {"limit_price": fmt_dec(target), "stop_trigger_price": fmt_dec(stop)}}


def order_configuration(order_type: str, base_size: str, limit_price=None) -> dict:
    if order_type == "limit":
        if not limit_price:
            raise CoinbaseError("a limit order needs a limit price")
        return {"limit_limit_gtc": {"base_size": base_size, "limit_price": fmt_dec(limit_price)}}
    return {"market_market_ioc": {"base_size": base_size}}


class CoinbaseClient:
    broker = "coinbase"

    def __init__(self, api_key_name: str = "", private_key: str = "", *, sdk=None, timeout: int = 15):
        self._key_name = api_key_name or ""
        secret = unescape_private_key(private_key)
        self.http = sdk if sdk is not None else self._make_sdk(self._key_name, secret, timeout)
        self.account: dict | None = None
        self._authed = False
        self._products: list | None = None
        self._details: dict = {}
        self._tracked: dict[str, dict] = {}

    def __repr__(self):
        acc = (self.account or {}).get("id")
        return f"<CoinbaseClient account={acc!r} authed={self.authed}>"

    @staticmethod
    def _make_sdk(api_key, api_secret, timeout):
        try:
            from coinbase.rest import RESTClient
        except ImportError as e:
            raise CoinbaseError("coinbase-advanced-py is not installed; run pip install -r bridge/requirements.txt") from e
        return RESTClient(api_key=api_key, api_secret=api_secret, timeout=timeout)

    @property
    def authed(self) -> bool:
        return bool(self._authed and self.account)

    def login(self) -> dict:
        if not self._key_name and self.http is None:
            raise CoinbaseError("Coinbase API key is not set in bridge/.env")
        try:
            accounts = self._list(_get(self.http.get_accounts(), "accounts"))
        except CoinbaseError:
            raise
        except Exception as e:
            raise CoinbaseError(self._clean(f"login failed: {e}")) from e
        cash, others = [], []
        for a in accounts:
            d = _as_dict(a)
            row = {
                "id": d.get("uuid") or d.get("id"),
                "currency": d.get("currency"),
                "available": _amt(d.get("available_balance")),
                "hold": _amt(d.get("hold")),
                "name": d.get("name"),
            }
            if (row["currency"] or "").upper() in CASH:
                cash.append(row)
            else:
                others.append(row)
        usd = next((x for x in cash if (x["currency"] or "").upper() == "USD"), cash[0] if cash else None)
        self.account = {
            "id": (usd or {}).get("id") or "coinbase",
            "accNum": None,
            "currency": (usd or {}).get("currency") or "USD",
            "status": "ACTIVE",
            "cash": cash,
            "balances": cash + others,
        }
        self._authed = True
        return self.account

    def _clean(self, msg: str) -> str:
        text = str(msg)
        if self._key_name:
            text = text.replace(self._key_name, "[key]")
        return text

    def _call(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except CoinbaseError:
            raise
        except Exception as e:
            raise CoinbaseError(self._clean(f"{getattr(fn, '__name__', 'request')} failed: {e}")) from e

    @staticmethod
    def _list(items) -> list:
        if items is None:
            return []
        if isinstance(items, list):
            return items
        return list(items)

    def _ensure(self):
        if not self.authed:
            self.login()

    # ---------------- reads ----------------
    def accounts(self) -> list[dict]:
        self._ensure()
        return list(self.account.get("balances") or [])

    def account_state(self) -> dict:
        self._ensure()
        # Re-read balances so the guard sees fresh equity.
        self.login()
        cash = 0.0
        for row in self.account.get("cash") or []:
            cash += float(row.get("available") or 0) + float(row.get("hold") or 0)
        equity = cash
        # Mark-to-market the non-cash balances we can price (best-effort; ignores unknown assets).
        for row in self.account.get("balances") or []:
            cur = (row.get("currency") or "").upper()
            qty = float(row.get("available") or 0) + float(row.get("hold") or 0)
            if cur in CASH or qty <= 0:
                continue
            pid = f"{cur}-USD"
            try:
                q = self.quote({"product_id": pid, "name": pid})
                mid = (q["ask"] + q["bid"]) / 2
                equity += qty * mid
            except CoinbaseError:
                pass
        return {"balance": round(cash, 8), "projectedBalance": round(equity, 8), "todayNet": None,
                "currency": self.account.get("currency")}

    def products(self) -> list[dict]:
        if self._products is None:
            raw = self._call(self.http.get_products, product_type="SPOT")
            items = self._list(_get(raw, "products"))
            if not items:  # some SDK mocks / older responses omit the type filter
                raw = self._call(self.http.get_products)
                items = self._list(_get(raw, "products"))
            self._products = [_as_dict(p) for p in items]
        return self._products

    def instruments(self) -> list[dict]:
        """Same role as TradeLocker's instruments(): a list of {name, ...}."""
        return [{"name": p.get("product_id"), **p} for p in self.products()]

    def find_instrument(self, symbol: str) -> dict:
        want = norm_product(DEFAULT_PRODUCTS.get(str(symbol or "").upper(), symbol))
        items = self.products()
        exact = [p for p in items if norm_product(p.get("product_id") or p.get("name") or "") == want]
        hit = exact[0] if exact else None
        if not hit:
            # fetch a single product in case the list was filtered
            try:
                hit = _as_dict(self._call(self.http.get_product, want))
            except CoinbaseError:
                hit = {}
        pid = hit.get("product_id") or hit.get("name")
        if not pid or (hit.get("status") or "").upper() in ("DELISTED", "DISABLED", "CANCEL_ONLY"):
            # still accept if we got a product_id and trading is allowed / status missing
            if not pid:
                raise CoinbaseError(f"product {symbol} is not available on Coinbase spot")
        if hit.get("trading_disabled") is True:
            raise CoinbaseError(f"product {pid} is not tradable on this account")
        return {"name": pid, "product_id": pid, "tradableInstrumentId": pid,
                "trade_route": None, "info_route": None,
                "description": hit.get("display_name") or pid}

    def instrument_details(self, inst: dict) -> dict:
        pid = inst["product_id"]
        if pid not in self._details:
            raw = _as_dict(self._call(self.http.get_product, pid))
            base_inc = raw.get("base_increment") or "0.00000001"
            quote_inc = raw.get("quote_increment") or "0.01"
            self._details[pid] = {
                "lotSize": 1,
                "lotStep": float(base_inc),
                "minLot": float(raw.get("base_min_size") or base_inc),
                "maxLot": float(raw["base_max_size"]) if raw.get("base_max_size") else None,
                "leverage": 1,
                "quoteMinSize": float(raw["quote_min_size"]) if raw.get("quote_min_size") else None,
                "quoteMaxSize": float(raw["quote_max_size"]) if raw.get("quote_max_size") else None,
                "base_increment": str(base_inc),
                "quote_increment": str(quote_inc),
                "base_min_size": raw.get("base_min_size"),
                "quote_min_size": raw.get("quote_min_size"),
                "base_max_size": raw.get("base_max_size"),
                "status": raw.get("status"),
                "product_type": raw.get("product_type") or "SPOT",
            }
        return self._details[pid]

    def quote(self, inst: dict) -> dict:
        pid = inst.get("product_id") or inst.get("name")
        raw = _as_dict(self._call(self.http.get_best_bid_ask, product_ids=[pid]))
        books = self._list(raw.get("pricebooks"))
        book = next((b for b in books if _get(b, "product_id") == pid), books[0] if books else None)
        if not book:
            raise CoinbaseError(f"no live quote for {pid}")
        book = _as_dict(book)
        ask = _amt((_as_dict((book.get("asks") or [{}])[0])).get("price") or (book.get("asks") or [None])[0])
        bid = _amt((_as_dict((book.get("bids") or [{}])[0])).get("price") or (book.get("bids") or [None])[0])
        # Some mocks use {asks:[{price,size}]} already handled; also allow strings on the book itself.
        if ask is None:
            ask = _amt(book.get("ask") or book.get("best_ask"))
        if bid is None:
            bid = _amt(book.get("bid") or book.get("best_bid"))
        if not ask or not bid or ask <= 0 or bid <= 0:
            raise CoinbaseError(f"no live quote for {pid}")
        return {"ask": float(ask), "bid": float(bid)}

    def positions(self) -> list[dict]:
        """Open hive orders we placed (spot has no CFD position id)."""
        self._ensure()
        return [dict(v) for v in self._tracked.values() if v.get("status") != "closed"]

    # ---------------- trading ----------------
    def _require_buy(self, side: str):
        if str(side or "").lower() != "buy":
            raise CoinbaseError("Coinbase spot is long-only: SHORT/SELL entry calls are refused.")

    def _size_str(self, inst: dict, qty) -> str:
        details = self.instrument_details(inst)
        stepped = floor_to_increment(qty, details.get("base_increment") or details.get("lotStep") or "0.00000001")
        min_lot = _dec(details.get("base_min_size") or details.get("minLot") or 0) or Decimal("0")
        if stepped <= 0 or (min_lot and stepped < min_lot):
            raise CoinbaseError(f"size {qty} rounded down to {fmt_dec(stepped)} is below the minimum "
                                f"{fmt_dec(min_lot)} for {inst.get('name')}; the bridge never rounds up.")
        return fmt_dec(stepped)

    def _price_str(self, inst: dict, price) -> str:
        details = self.instrument_details(inst)
        inc = details.get("quote_increment") or "0.01"
        return fmt_dec(floor_to_increment(price, inc)) if _dec(price) else fmt_dec(price)

    def _body(self, inst, side, qty, stop, target, order_type="market", limit_price=None) -> dict:
        self._require_buy(side)
        size = self._size_str(inst, qty)
        if order_type == "limit":
            lp = self._price_str(inst, limit_price or inst.get("limit_price"))
            cfg = order_configuration("limit", size, lp)
        else:
            cfg = order_configuration("market", size)
        # Floor TP/SL to the quote increment (down for sizes; for prices keep the intended side
        # by formatting the already-validated absolute levels to the increment without rounding up).
        details = self.instrument_details(inst)
        qinc = details.get("quote_increment") or "0.01"
        sl = fmt_dec(floor_to_increment(stop, qinc))
        tp = fmt_dec(floor_to_increment(target, qinc))
        return {
            "product_id": inst["product_id"],
            "side": "BUY",
            "order_configuration": cfg,
            "attached_order_configuration": attached_bracket(sl, tp),
        }

    def preview_order(self, inst: dict, side: str, qty, stop, target, order_type="market",
                      limit_price=None) -> dict:
        body = self._body(inst, side, qty, stop, target, order_type, limit_price)
        raw = _as_dict(self._call(self.http.preview_order, **body))
        errs = raw.get("errs") or raw.get("errors") or []
        if errs:
            raise CoinbaseError(self._clean(f"preview rejected: {errs}"))
        return raw

    def place_market_order(self, inst: dict, side: str, qty_lots: float, stop_loss: float,
                           take_profit: float, strategy_id: str = STRATEGY_TAG,
                           order_type: str = "market", limit_price=None,
                           client_ref: str = "") -> str:
        body = self._body(inst, side, qty_lots, stop_loss, take_profit, order_type, limit_price)
        cid = (client_ref or str(uuid.uuid4()))[:36] or str(uuid.uuid4())
        raw = _as_dict(self._call(self.http.create_order, client_order_id=cid, **body))
        if raw.get("success") is False or (raw.get("error_response") and not raw.get("success")):
            err = _as_dict(raw.get("error_response"))
            raise CoinbaseError(self._clean(err.get("message") or err.get("error") or "create_order failed"))
        oid = raw.get("order_id") or _as_dict(raw.get("success_response")).get("order_id")
        if not oid:
            raise CoinbaseError("create_order returned no order id")
        self._tracked[str(oid)] = {
            "id": str(oid), "tradableInstrumentId": inst["product_id"], "product_id": inst["product_id"],
            "side": "buy", "qty": qty_lots, "stop": stop_loss, "takeProfit": take_profit,
            "status": "open", "strategyId": (strategy_id or STRATEGY_TAG)[:31],
        }
        return str(oid)

    def find_new_position(self, inst, known: set, tries: int = 1, wait: float = 0):
        for p in self.positions():
            if str(p.get("id")) not in known and str(p.get("product_id")) == str(inst.get("product_id")):
                return str(p.get("id"))
        return None

    def close_position(self, position_id: str) -> None:
        """Cancel a still-open hive order. Never market-sell the whole Coinbase wallet."""
        pid = str(position_id)
        rec = self._tracked.get(pid)
        try:
            self._call(self.http.cancel_orders, order_ids=[pid])
        except CoinbaseError:
            # A filled order with an attached bracket may already be gone; still mark closed.
            if rec is None:
                raise
        if rec:
            rec["status"] = "closed"

    def modify_position(self, position_id: str, stop_loss=None, take_profit=None) -> None:
        raise CoinbaseError("Coinbase attached brackets cannot be patched in place; cancel and place a new order.")
