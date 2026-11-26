"""Minimal TradeLocker REST client (public-api.tradelocker.com), used only by the bridge.

Auth: POST /auth/jwt/token {email, password, server} -> JWT. Every /trade/* call carries
'Authorization: Bearer <accessToken>' and the 'accNum' header. Market orders are IOC with
one absolute stop loss and one absolute take profit. Table-shaped responses (positions,
account state) are mapped to dicts using the column ids from /trade/config.
"""
from __future__ import annotations

import re
import time

import requests

TIMEOUT = 15
STRATEGY_TAG = "alpha-hive"


class TradeLockerError(RuntimeError):
    pass


def norm_symbol(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


class TradeLockerClient:
    def __init__(self, base_url: str, email: str, password: str, server: str, *, account_id: str = "",
                 acc_num: str = "", developer_api_key: str = "", session=None):
        self.base_url = base_url.rstrip("/")
        self._email, self._password, self.server = email, password, server
        self._want_id, self._want_num = str(account_id or ""), str(acc_num or "")
        self._dev_key = developer_api_key
        self.http = session or requests.Session()
        self._access = self._refresh = None
        self.account: dict | None = None
        self._config: dict | None = None
        self._instruments: list | None = None
        self._details: dict = {}
        self.backoff = 1.0

    def __repr__(self):  # never print credentials or tokens
        acc = (self.account or {}).get("id")
        return f"<TradeLockerClient {self.base_url} server={self.server!r} account={acc!r} authed={bool(self._access)}>"

    # ---------------- auth ----------------
    @property
    def authed(self) -> bool:
        return bool(self._access and self.account)

    def login(self) -> dict:
        if not (self._email and self._password and self.server):
            raise TradeLockerError("TradeLocker email, password and server are not set in bridge/.env")
        r = self.http.post(f"{self.base_url}/auth/jwt/token", timeout=TIMEOUT,
                           json={"email": self._email, "password": self._password, "server": self.server})
        if r.status_code not in (200, 201):
            raise TradeLockerError(f"login failed (HTTP {r.status_code})")
        j = r.json()
        self._access, self._refresh = j.get("accessToken"), j.get("refreshToken")
        if not self._access:
            raise TradeLockerError("login failed: no access token in the response")
        self.account = self._pick_account(self.accounts())
        return self.account

    def _refresh_tokens(self) -> bool:
        if not self._refresh:
            return False
        r = self.http.post(f"{self.base_url}/auth/jwt/refresh", json={"refreshToken": self._refresh}, timeout=TIMEOUT)
        if r.status_code not in (200, 201):
            return False
        j = r.json(); self._access, self._refresh = j.get("accessToken"), j.get("refreshToken", self._refresh)
        return bool(self._access)

    def accounts(self) -> list[dict]:
        r = self.http.get(f"{self.base_url}/auth/jwt/all-accounts", headers=self._auth(), timeout=TIMEOUT)
        if r.status_code != 200:
            raise TradeLockerError(f"could not list accounts (HTTP {r.status_code})")
        return r.json().get("accounts") or []

    def _pick_account(self, accounts: list[dict]) -> dict:
        if not accounts:
            raise TradeLockerError("no TradeLocker accounts on this login")
        for a in accounts:
            if self._want_id and str(a.get("id")) == self._want_id:
                return a
            if self._want_num and str(a.get("accNum")) == self._want_num and not self._want_id:
                return a
        if self._want_id or self._want_num:
            raise TradeLockerError("the account set in TRADELOCKER_ACCOUNT_ID / TRADELOCKER_ACC_NUM was not found")
        return accounts[0]

    def _auth(self) -> dict:
        h = {"Authorization": f"Bearer {self._access}", "accept": "application/json"}
        if self._dev_key:
            h["developer-api-key"] = self._dev_key
        return h

    def _hdr(self) -> dict:
        return {**self._auth(), "accNum": str(self.account["accNum"])}

    def _req(self, method: str, path: str, **kw):
        if not self.authed:
            self.login()
        for attempt in range(4):
            r = self.http.request(method, f"{self.base_url}{path}", headers=self._hdr(), timeout=TIMEOUT, **kw)
            if r.status_code == 401 and attempt == 0:
                if not self._refresh_tokens():
                    self.login()
                continue
            if r.status_code == 429 and attempt < 3 and method == "GET":   # rate limited: back off (reads only)
                time.sleep(self.backoff * (attempt + 1))
                continue
            break
        if r.status_code >= 400:
            msg = ""
            try:
                msg = r.json().get("errmsg") or r.json().get("message") or ""
            except ValueError:
                pass
            raise TradeLockerError(f"{method} {path.split('?')[0]} failed (HTTP {r.status_code}) {msg}".strip())
        j = r.json() if r.content else {}
        if isinstance(j, dict) and j.get("s") not in (None, "ok"):
            raise TradeLockerError(f"{method} {path.split('?')[0]}: {j.get('errmsg') or j.get('s')}")
        return j

    @property
    def acc_id(self) -> str:
        if not self.authed:
            self.login()
        return str(self.account["id"])

    # ---------------- reads ----------------
    def config(self) -> dict:
        if self._config is None:
            self._config = self._req("GET", "/trade/config").get("d", {})
        return self._config

    def _columns(self, key: str) -> list[str]:
        cols = (self.config().get(key) or {}).get("columns") or []
        return [c.get("id") for c in cols]

    def instruments(self) -> list[dict]:
        if self._instruments is None:
            self._instruments = self._req("GET", f"/trade/accounts/{self.acc_id}/instruments").get("d", {}).get("instruments", [])
        return self._instruments

    def find_instrument(self, symbol: str) -> dict:
        want = norm_symbol(symbol)
        items = self.instruments()
        exact = [i for i in items if norm_symbol(i.get("name")) == want]
        loose = [i for i in items if norm_symbol(i.get("name")).startswith(want)]
        hit = (exact or sorted(loose, key=lambda i: len(i.get("name", ""))) or [None])[0]
        if not hit:
            raise TradeLockerError(f"instrument {symbol} is not available on this account")
        routes = {r.get("type"): r.get("id") for r in hit.get("routes", [])}
        if "TRADE" not in routes:
            raise TradeLockerError(f"instrument {hit.get('name')} has no TRADE route")
        return {"name": hit.get("name"), "tradableInstrumentId": int(hit["tradableInstrumentId"]),
                "trade_route": int(routes["TRADE"]), "info_route": int(routes.get("INFO", routes["TRADE"])),
                "description": hit.get("description", "")}

    def instrument_details(self, inst: dict) -> dict:
        key = inst["tradableInstrumentId"]
        if key not in self._details:
            d = self._req("GET", f"/trade/instruments/{key}?routeId={inst['trade_route']}").get("d", {})
            self._details[key] = d
        return self._details[key]

    def quote(self, inst: dict) -> dict:
        d = self._req("GET", f"/trade/quotes?routeId={inst['info_route']}&tradableInstrumentId={inst['tradableInstrumentId']}").get("d", {})
        ask, bid = d.get("ap"), d.get("bp")
        if not isinstance(ask, (int, float)) or not isinstance(bid, (int, float)) or ask <= 0 or bid <= 0:
            raise TradeLockerError("no live quote for this instrument")
        return {"ask": float(ask), "bid": float(bid)}

    def account_state(self) -> dict:
        row = self._req("GET", f"/trade/accounts/{self.acc_id}/state").get("d", {}).get("accountDetailsData", [])
        cols = self._columns("accountDetailsConfig")
        out = dict(zip(cols, row)) if cols else {}
        out.setdefault("balance", row[0] if row else None)
        return out

    def positions(self) -> list[dict]:
        rows = self._req("GET", f"/trade/accounts/{self.acc_id}/positions").get("d", {}).get("positions", [])
        cols = self._columns("positionsConfig") or ["id", "tradableInstrumentId", "routeId", "side", "qty",
                                                     "avgPrice", "stopLossId", "takeProfitId", "openDate",
                                                     "unrealizedPl", "strategyId"]
        return [dict(zip(cols, r)) for r in rows]

    # ---------------- trading ----------------
    def place_market_order(self, inst: dict, side: str, qty_lots: float, stop_loss: float, take_profit: float,
                           strategy_id: str = STRATEGY_TAG) -> str:
        if side not in ("buy", "sell"):
            raise TradeLockerError("side must be buy or sell")
        body = {"tradableInstrumentId": inst["tradableInstrumentId"], "routeId": inst["trade_route"],
                "side": side, "type": "market", "validity": "IOC", "price": 0, "qty": qty_lots,
                "stopLoss": stop_loss, "stopLossType": "absolute",
                "takeProfit": take_profit, "takeProfitType": "absolute", "strategyId": strategy_id[:31]}
        d = self._req("POST", f"/trade/accounts/{self.acc_id}/orders", json=body).get("d", {})
        return str(d.get("orderId"))

    def modify_position(self, position_id: str, stop_loss=None, take_profit=None) -> None:
        body = {}
        if stop_loss is not None:
            body["stopLoss"] = stop_loss
        if take_profit is not None:
            body["takeProfit"] = take_profit
        if not body:
            raise TradeLockerError("nothing to modify")
        self._req("PATCH", f"/trade/positions/{position_id}", json=body)

    def close_position(self, position_id: str) -> None:
        self._req("DELETE", f"/trade/positions/{position_id}", json={"qty": 0})

    def find_new_position(self, inst: dict, known: set, tries: int = 3, wait: float = 0.5):
        for _ in range(tries):
            for p in self.positions():
                if str(p.get("id")) not in known and str(p.get("tradableInstrumentId")) == str(inst["tradableInstrumentId"]):
                    return str(p.get("id"))
            time.sleep(wait)
        return None
