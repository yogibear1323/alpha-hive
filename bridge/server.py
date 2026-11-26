"""FastAPI bridge: the only path from the hive to a broker (TradeLocker or Coinbase). Binds 127.0.0.1 only.

Every route except /health needs the X-Bridge-Token header (BRIDGE_TOKEN from bridge/.env).
Browsers may call it only from http://localhost or http://127.0.0.1 pages (CORS), so the
public GitHub Pages site can never reach it.
"""
from __future__ import annotations

import hmac
import json
import time
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from bridge.coinbase import CoinbaseClient, CoinbaseError
from bridge.guard import Blocked, GuardState
from bridge.settings import LIVE_CONFIRM_PHRASE, Settings
from bridge import sizing
from bridge.tradelocker import TradeLockerClient, TradeLockerError

BROKER_ERRORS = (TradeLockerError, CoinbaseError)

LOCAL_ORIGINS = r"^http://(localhost|127\.0\.0\.1)(:\d{1,5})?$"
WATCH = ("BTCUSD", "ETHUSD", "US30")
DIRECTIONAL = {"buy": ("BUY",), "sell": ("SELL", "SHORT")}


class RiskVerdict(BaseModel):
    status: Literal["approve", "reduce", "reject"]
    ceiling_pct: Optional[float] = None


class QueenCall(BaseModel):
    call: str


class OrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    side: Literal["buy", "sell"]
    stop: float
    target: float
    risk_pct: Optional[float] = None
    entry_ref: Optional[float] = None
    risk: RiskVerdict
    queen: QueenCall
    client_ref: str = Field(default="", max_length=60)
    dry_run: bool = False
    order_type: Literal["market", "limit"] = "market"


class ModifyIn(BaseModel):
    stop: Optional[float] = None
    target: Optional[float] = None


class KillIn(BaseModel):
    close_all: bool = False
    reason: str = Field(default="", max_length=200)


class ModeIn(BaseModel):
    mode: Literal["DEMO", "LIVE", "PREVIEW"]
    confirm: str = ""


def create_app(settings: Settings, client=None, guard: GuardState | None = None) -> FastAPI:
    app = FastAPI(title="Alpha Hive bridge", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origin_regex=LOCAL_ORIGINS, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-Bridge-Token"])
    st = {"client": client, "live_armed": False, "last_error": None}
    guard = guard or GuardState(settings.state_dir, settings.limits, settings.kill_switch_env)
    audit_path = settings.state_dir / "orders.jsonl"

    def audit(entry: dict):
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), "env": settings.env, **entry}) + "\n")

    def is_coinbase() -> bool:
        c = st["client"]
        if c is not None:
            return getattr(c, "broker", None) == "coinbase"
        return settings.broker == "coinbase"

    def tl():
        if st["client"] is None:
            if settings.broker == "coinbase":
                st["client"] = CoinbaseClient(settings.coinbase_api_key_name, settings.coinbase_api_private_key)
            else:
                st["client"] = TradeLockerClient(settings.base_url, settings.email, settings.password, settings.server,
                                                 account_id=settings.account_id, acc_num=settings.acc_num,
                                                 developer_api_key=settings.developer_api_key)
        return st["client"]

    def token(x_bridge_token: str = Header(default="")):
        if not hmac.compare_digest(x_bridge_token.encode(), settings.bridge_token.encode()):
            raise HTTPException(401, "missing or wrong X-Bridge-Token")

    def broker_call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except BROKER_ERRORS as e:
            st["last_error"] = str(e)
            label = "Coinbase" if isinstance(e, CoinbaseError) else "TradeLocker"
            raise HTTPException(502, f"{label}: {e}")

    def mode() -> str:
        if is_coinbase():
            return "LIVE" if settings.allow_live and st["live_armed"] else "PREVIEW"
        return "LIVE" if settings.env == "live" and st["live_armed"] else "DEMO" if settings.env == "demo" else "LIVE_DISARMED"

    @app.get("/health")
    def health():
        return {"ok": True, "service": "alpha-hive-bridge"}

    @app.get("/status", dependencies=[Depends(token)])
    def status():
        cb = is_coinbase()
        out = {"broker": "Coinbase Advanced Trade" if cb else "Crucial Markets (TradeLocker)",
               "broker_id": "coinbase" if cb else "tradelocker",
               "backend": settings.env if not cb else ("live" if mode() == "LIVE" else "preview"),
               "mode": mode(), "live_allowed": settings.allow_live, "live_armed": st["live_armed"],
               "credentials_set": settings.has_credentials, "server": None if cb else (settings.server or None),
               "kill_switch": guard.kill_engaged(), "limits": settings.limits.__dict__,
               "connected": False, "account": None, "last_error": st["last_error"],
               "spot_long_only": cb, "preview_only": cb and mode() != "LIVE"}
        if settings.has_credentials:
            try:
                c = tl()
                if not c.authed:
                    c.login()
                a = c.account or {}
                state = c.account_state()
                out.update(connected=True, account={"id": a.get("id"), "accNum": a.get("accNum"),
                                                    "currency": a.get("currency"), "status": a.get("status"),
                                                    "balance": state.get("balance"),
                                                    "equity": state.get("projectedBalance", state.get("balance"))},
                           daily=guard.daily_status(state.get("projectedBalance", state.get("balance")),
                                                    state.get("todayNet")))
            except BROKER_ERRORS as e:
                st["last_error"] = out["last_error"] = str(e)
        return out

    @app.get("/instruments", dependencies=[Depends(token)])
    def instruments(q: str = ""):
        from bridge.coinbase import WATCH as CB_WATCH
        items = broker_call(tl().instruments)
        names = [i.get("name") for i in items]
        found = {}
        for s in (CB_WATCH if is_coinbase() else WATCH):
            try:
                found[s] = tl().find_instrument(s)
            except BROKER_ERRORS:
                found[s] = None
        needle = q.upper().replace("/", "").replace("-", "")
        match = [n for n in names if q and needle in str(n).upper().replace("/", "").replace("-", "")][:50]
        return {"count": len(items), "watch": found, "match": match}

    @app.get("/account", dependencies=[Depends(token)])
    def account():
        return broker_call(tl().account_state)

    @app.get("/positions", dependencies=[Depends(token)])
    def positions():
        return {"positions": broker_call(tl().positions)}

    @app.post("/orders", dependencies=[Depends(token)])
    def place(o: OrderIn):
        ref = {"symbol": o.symbol, "side": o.side, "client_ref": o.client_ref}
        try:
            # 1. The guard's verdict comes first and cannot be overruled.
            if o.risk.status != "approve":
                raise Blocked("risk_not_approved", f"Risk said '{o.risk.status}': only an approve can reach the broker.")
            if not any(k in o.queen.call.upper() for k in DIRECTIONAL[o.side]):
                raise Blocked("call_mismatch", f"The Queen's call '{o.queen.call}' does not match side '{o.side}'.")
            cb = is_coinbase()
            if cb and o.side != "buy":
                raise Blocked("spot_long_only",
                              "Coinbase spot is long-only: SHORT/SELL calls are refused (no margin or perps).")
            if settings.env == "live" and not st["live_armed"] and not cb:
                raise Blocked("live_not_armed", "LIVE backend is not armed: confirm LIVE in the terminal first.")
            if cb:
                symbol = settings.coinbase_symbols.get(o.symbol.upper()) or settings.coinbase_symbols.get(o.symbol)
                if not symbol and "-" in o.symbol:
                    symbol = o.symbol.upper()
            else:
                symbol = settings.symbols.get(o.symbol.upper())
            if not symbol:
                where = "a Coinbase spot product (COINBASE_SYMBOL_MAP)" if cb else "a Crucial instrument (SYMBOL_MAP)"
                raise Blocked("symbol_not_mapped", f"{o.symbol} is not mapped to {where}.")
            c = tl()
            state = broker_call(c.account_state)
            equity = state.get("projectedBalance", state.get("balance"))
            open_pos = broker_call(c.positions)
            guard.pre_trade(equity, state.get("todayNet"), len(open_pos))
            inst = broker_call(c.find_instrument, symbol)
            details = broker_call(c.instrument_details, inst)
            q = broker_call(c.quote, inst)
            entry = q["ask"] if o.side == "buy" else q["bid"]
            pct = o.risk_pct
            if o.risk.ceiling_pct is not None and pct is not None:
                pct = min(pct, o.risk.ceiling_pct)
            p = sizing.plan(balance=state.get("balance"), side=o.side, entry=entry, stop=o.stop, target=o.target,
                            risk_pct=pct, limits=settings.limits, details=details, entry_ref=o.entry_ref)
        except Blocked as b:
            audit({**ref, "result": "blocked", "code": b.code, "reason": str(b)})
            raise HTTPException(409 if b.code != "kill_switch" else 423, {"blocked": b.code, "reason": str(b)})
        p.update(instrument=inst["name"], symbol=symbol)
        preview = cb and (o.dry_run or mode() != "LIVE")
        if o.dry_run and not cb:
            audit({**ref, "result": "dry_run", "plan": p})
            return {"placed": False, "dry_run": True, "plan": p, "mode": mode()}
        if preview:
            preview_body = broker_call(c.preview_order, inst, o.side, p["lots"], p["stop"], p["target"],
                                       o.order_type, o.entry_ref if o.order_type == "limit" else None)
            audit({**ref, "result": "preview", "plan": p})
            return {"placed": False, "dry_run": bool(o.dry_run), "preview": True, "preview_result": preview_body,
                    "plan": p, "mode": mode()}
        known = {str(x.get("id")) for x in open_pos}
        if cb:
            order_id = broker_call(c.place_market_order, inst, o.side, p["lots"], p["stop"], p["target"],
                                   "alpha-hive", o.order_type,
                                   o.entry_ref if o.order_type == "limit" else None, o.client_ref)
        else:
            order_id = broker_call(c.place_market_order, inst, o.side, p["lots"], p["stop"], p["target"])
        pos_id = None
        try:
            pos_id = c.find_new_position(inst, known)
        except BROKER_ERRORS:
            pass
        audit({**ref, "result": "placed", "order_id": order_id, "position_id": pos_id, "plan": p})
        return {"placed": True, "order_id": order_id, "position_id": pos_id, "plan": p, "mode": mode()}

    @app.patch("/positions/{position_id}", dependencies=[Depends(token)])
    def modify(position_id: str, m: ModifyIn):
        broker_call(tl().modify_position, position_id, m.stop, m.target)
        audit({"result": "modified", "position_id": position_id, "stop": m.stop, "target": m.target})
        return {"ok": True}

    @app.delete("/positions/{position_id}", dependencies=[Depends(token)])
    def close(position_id: str, reason: str = ""):
        broker_call(tl().close_position, position_id)
        audit({"result": "closed", "position_id": position_id, "reason": reason[:120]})
        return {"ok": True}

    @app.post("/kill", dependencies=[Depends(token)])
    def kill(k: KillIn):
        guard.engage_kill(k.reason or "engaged over HTTP")
        st["live_armed"] = False
        closed, errors = [], []
        if k.close_all:
            for pos in broker_call(tl().positions):
                try:
                    tl().close_position(str(pos.get("id"))); closed.append(str(pos.get("id")))
                except BROKER_ERRORS as e:
                    errors.append(str(e))
        audit({"result": "kill_switch", "closed": closed, "errors": errors})
        return {"kill_switch": True, "closed": closed, "errors": errors,
                "release": "python -m bridge release-kill (on this machine)"}

    @app.post("/mode", dependencies=[Depends(token)])
    def set_mode(m: ModeIn):
        if m.mode in ("DEMO", "PREVIEW"):
            st["live_armed"] = False
            return {"mode": mode()}
        if is_coinbase():
            if not settings.allow_live:
                raise HTTPException(403, "LIVE is off: Coinbase has no demo account. The bridge stays in PREVIEW "
                                         "(preview_order only) until you set ALPHA_HIVE_ALLOW_LIVE=1 in bridge/.env "
                                         "and restart, then arm LIVE here.")
        elif settings.env != "live" or not settings.allow_live:
            raise HTTPException(403, "LIVE is off: the bridge runs on the demo backend. It needs TRADELOCKER_ENV=live "
                                     "and ALPHA_HIVE_ALLOW_LIVE=1 in bridge/.env and a restart.")
        if m.confirm != LIVE_CONFIRM_PHRASE:
            raise HTTPException(400, f"To arm LIVE send confirm='{LIVE_CONFIRM_PHRASE}'.")
        if guard.kill_engaged():
            raise HTTPException(423, "Kill switch is engaged.")
        st["live_armed"] = True
        audit({"result": "live_armed"})
        return {"mode": mode()}

    app.state.bridge = st
    app.state.guard = guard
    return app
