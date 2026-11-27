"""Session unlock, Fund-panel, and Stellar DEX live-session routes.

Unlock authenticators: passphrase today; wallet-signature (SEP-10 style challenge)
issues the same short-lived session. Both can be enabled. Protected routes accept
X-Session-Token or the existing X-Bridge-Token (CLI / tests). Responses never
include secret keys, hashes, or raw session material beyond the one-time token
from /unlock.
"""
from __future__ import annotations

import time
from typing import Literal, Optional

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field

from bridge.envfile import upsert_env
from bridge.guard import Blocked
from bridge.session import (
    LockedOut, hash_passphrase, verify_passphrase,
)
from bridge.settings import LIVE_CONFIRM_PHRASE
from bridge import sizing
from bridge.stellar_dex import (
    can_submit, fetch_account, is_valid_address, min_received,
    parse_strict_send_quote, qr_svg, quote_xlm_to_usdc, quote_usdc_to_xlm,
    sanitize_out, usdc_issuer, horizon_url, normalize_network,
    build_change_trust_xdr, build_path_payment_xdr, sign_xdr, submit_xdr,
    is_secret_key,
)


class PassphraseIn(BaseModel):
    passphrase: str = Field(min_length=1, max_length=200)


class ChallengeIn(BaseModel):
    address: str = Field(min_length=56, max_length=56)


class WalletUnlockIn(BaseModel):
    address: str = Field(min_length=56, max_length=56)
    challenge_id: str = Field(min_length=4, max_length=80)
    signature: str = Field(min_length=8, max_length=200)


class WalletSetupIn(BaseModel):
    address: str = Field(min_length=56, max_length=56)


class BotSetupIn(BaseModel):
    allowlist: str = ""
    rotate: bool = False


class QuoteIn(BaseModel):
    direction: Literal["xlm_to_usdc", "usdc_to_xlm"] = "xlm_to_usdc"
    send_amount: str = Field(min_length=1, max_length=32)
    slippage_pct: float = 0.5


class ConvertIn(BaseModel):
    direction: Literal["xlm_to_usdc", "usdc_to_xlm"] = "xlm_to_usdc"
    send_amount: str = Field(min_length=1, max_length=32)
    slippage_pct: float = 0.5
    confirm: bool = False
    signer: Literal["bot", "wallet"] = "bot"


class TrustlineIn(BaseModel):
    signer: Literal["bot", "wallet"] = "bot"
    confirm: bool = False


class WithdrawIn(BaseModel):
    destination: str = Field(min_length=56, max_length=56)
    amount: str = Field(min_length=1, max_length=32)
    asset: Literal["XLM", "USDC"] = "XLM"
    confirm: bool = False


class StellarModeIn(BaseModel):
    mode: Literal["OFF", "TESTNET", "LIVE"]
    confirm: str = ""


class StellarOrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    side: Literal["buy", "sell"]
    stop: float
    target: float
    risk_pct: Optional[float] = None
    entry_ref: Optional[float] = None
    entry: Optional[float] = None
    balance: Optional[float] = None
    risk: dict
    queen: dict
    client_ref: str = Field(default="", max_length=60)
    signer: Literal["bot", "wallet"] = "bot"
    dry_run: bool = False


def _http_locked(e: LockedOut) -> HTTPException:
    return HTTPException(429, f"Too many failed unlocks. Try again in {e.retry_after} seconds.")


def register(app, settings, st, sessions, challenges, unlock_guard, require_auth, audit, stellar_guard):
    def _env_path():
        return settings.dotenv_path

    def _disarm():
        st["live_armed"] = False
        st["stellar_armed"] = False
        st["stellar_testnet_armed"] = False

    def _session_payload(token: str, exp: float, method: str):
        idle = settings.session_idle_seconds
        return {
            "ok": True,
            "token": token,
            "expires_in": idle,
            "method": method,
            "live_armed": st["live_armed"],
            "stellar_armed": st["stellar_armed"],
            "stellar_testnet_armed": st["stellar_testnet_armed"],
        }

    def _unlock_methods():
        methods = []
        if st.get("passphrase_hash"):
            methods.append("passphrase")
        methods.append("wallet")
        return methods

    def _horizon():
        return st.get("horizon")

    def _quote(direction: str, send_amount: str, slippage_pct: float):
        hook = st.get("quote_fn")
        if hook:
            return hook(direction, send_amount, slippage_pct)
        net = settings.stellar_network
        http = _horizon()
        if direction == "usdc_to_xlm":
            q = quote_usdc_to_xlm(net, send_amount, client=http)
        else:
            q = quote_xlm_to_usdc(net, send_amount, client=http)
        if q.get("ok") and "dest_min" not in q:
            q = {**q, "dest_min": min_received(q.get("dest_amount"), slippage_pct)}
        return q

    def _account(address: str):
        hook = st.get("account_fn")
        if hook:
            return hook(address)
        return fetch_account(settings.stellar_network, address, client=_horizon())

    def _submit(xdr: str):
        hook = st.get("submitter")
        return submit_xdr(settings.stellar_network, xdr, client=hook)

    def _balances(account_body: dict) -> dict:
        xlm = usdc = "0"
        has_trust = False
        issuer = usdc_issuer(settings.stellar_network)
        for b in (account_body or {}).get("balances") or []:
            if b.get("asset_type") == "native":
                xlm = str(b.get("balance") or "0")
            elif b.get("asset_code") == "USDC" and b.get("asset_issuer") == issuer:
                usdc = str(b.get("balance") or "0")
                has_trust = True
            elif b.get("asset_code") == "USDC":
                has_trust = True
        return {"xlm": xlm, "usdc": usdc, "has_usdc_trustline": has_trust}

    @app.get("/setup/status")
    def setup_status():
        bot = None
        if st.get("bot_public"):
            bot = {
                "public_key": st["bot_public"],
                "network": settings.stellar_network,
                "allowlist": st.get("allowlist") or "",
            }
        return sanitize_out({
            "ok": True,
            "passphrase_set": bool(st.get("passphrase_hash")),
            "unlock_methods": _unlock_methods(),
            "bot_account": bot,
            "session_idle_seconds": settings.session_idle_seconds,
            "stellar_network": settings.stellar_network,
            "live_allowed": settings.allow_live,
            "stellar_limits": settings.stellar_limits.__dict__,
        })

    @app.post("/setup/passphrase")
    def setup_passphrase(body: PassphraseIn):
        if st.get("passphrase_hash"):
            raise HTTPException(409, "A passphrase is already set. Change it with python -m bridge set-passphrase.")
        try:
            hashed = hash_passphrase(body.passphrase)
        except ValueError as e:
            raise HTTPException(400, str(e))
        path = _env_path()
        if path:
            upsert_env(path, "ALPHA_HIVE_PASSPHRASE_HASH", hashed)
        st["passphrase_hash"] = hashed
        audit({"result": "passphrase_set", "method": "passphrase"})
        return {"ok": True, "passphrase_set": True, "unlock_methods": _unlock_methods()}

    @app.post("/unlock")
    def unlock_passphrase(body: PassphraseIn):
        try:
            unlock_guard.assert_not_locked()
        except LockedOut as e:
            raise _http_locked(e)
        if not st.get("passphrase_hash"):
            raise HTTPException(
                403,
                "No unlock passphrase is set. Run python -m bridge set-passphrase, "
                "or unlock with a wallet signature (POST /unlock/challenge then /unlock/wallet).",
            )
        if not verify_passphrase(st["passphrase_hash"], body.passphrase):
            stt = unlock_guard.failure()
            if stt["locked"]:
                raise _http_locked(LockedOut(stt["retry_after"]))
            raise HTTPException(401, "wrong passphrase")
        unlock_guard.success()
        token, exp = sessions.issue(settings.session_idle_seconds, method="passphrase")
        return _session_payload(token, exp, "passphrase")

    @app.post("/unlock/challenge")
    def unlock_challenge(body: ChallengeIn):
        try:
            unlock_guard.assert_not_locked()
        except LockedOut as e:
            raise _http_locked(e)
        if not is_valid_address(body.address):
            raise HTTPException(400, "address must be a public G... address")
        try:
            ch = challenges.issue(body.address, settings.stellar_network)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, **ch}

    @app.post("/unlock/wallet")
    def unlock_wallet(body: WalletUnlockIn):
        try:
            unlock_guard.assert_not_locked()
        except LockedOut as e:
            raise _http_locked(e)
        try:
            challenges.consume(body.challenge_id, body.address, body.signature)
        except ValueError:
            stt = unlock_guard.failure()
            if stt["locked"]:
                raise _http_locked(LockedOut(stt["retry_after"]))
            raise HTTPException(401, "wallet unlock failed")
        unlock_guard.success()
        if not (st.get("allowlist") or "").strip():
            st["allowlist"] = body.address.strip()
            path = _env_path()
            if path:
                upsert_env(path, "STELLAR_WITHDRAW_ALLOWLIST", st["allowlist"])
        token, exp = sessions.issue(settings.session_idle_seconds, method="wallet")
        return _session_payload(token, exp, "wallet")

    @app.post("/logout")
    def logout(
        x_session_token: str = Header(default="", alias="X-Session-Token"),
        x_bridge_token: str = Header(default="", alias="X-Bridge-Token"),
    ):
        sessions.drop(x_session_token)
        _disarm()
        return {"ok": True, "live_armed": False, "stellar_armed": False, "stellar_testnet_armed": False}

    @app.get("/session", dependencies=[Depends(require_auth)])
    def session_info():
        return {
            "ok": True,
            "unlocked": True,
            "passphrase_set": bool(st.get("passphrase_hash")),
            "unlock_methods": _unlock_methods(),
            "live_armed": st["live_armed"],
            "stellar_armed": st["stellar_armed"],
            "stellar_testnet_armed": st["stellar_testnet_armed"],
            "bot_account": ({"public_key": st["bot_public"], "network": settings.stellar_network,
                             "allowlist": st.get("allowlist") or ""} if st.get("bot_public") else None),
            "session_idle_seconds": settings.session_idle_seconds,
            "stellar_network": settings.stellar_network,
        }

    @app.post("/setup/wallet", dependencies=[Depends(require_auth)])
    def setup_wallet(body: WalletSetupIn):
        if not is_valid_address(body.address):
            raise HTTPException(400, "address must be a public G... address")
        st["allowlist"] = body.address.strip()
        path = _env_path()
        if path:
            upsert_env(path, "STELLAR_WITHDRAW_ALLOWLIST", st["allowlist"])
        return {"ok": True, "allowlist": st["allowlist"]}

    @app.post("/setup/bot-account", dependencies=[Depends(require_auth)])
    def setup_bot(body: BotSetupIn):
        if st.get("bot_public") and not body.rotate:
            return {"ok": True, "public_key": st["bot_public"], "created": False,
                    "allowlist": st.get("allowlist") or "", "network": settings.stellar_network}
        allow = (body.allowlist or st.get("allowlist") or "").strip()
        if allow and not is_valid_address(allow):
            raise HTTPException(400, "allowlist must be a public G... address")
        from stellar_sdk import Keypair
        kp = Keypair.random()
        pub, secret = kp.public_key, kp.secret
        path = _env_path()
        if path:
            upsert_env(path, "STELLAR_BOT_PUBLIC_KEY", pub)
            upsert_env(path, "STELLAR_BOT_SECRET", secret)
            if allow:
                upsert_env(path, "STELLAR_WITHDRAW_ALLOWLIST", allow)
        st["bot_public"] = pub
        st["bot_secret"] = secret
        if allow:
            st["allowlist"] = allow
        audit({"result": "bot_account_created", "public_key": pub})
        return sanitize_out({"ok": True, "public_key": pub, "created": True,
                             "allowlist": st.get("allowlist") or "", "network": settings.stellar_network})

    @app.get("/setup/bot-account", dependencies=[Depends(require_auth)])
    def get_bot():
        if not st.get("bot_public"):
            return {"ok": True, "bot_account": None}
        return {"ok": True, "bot_account": {
            "public_key": st["bot_public"],
            "network": settings.stellar_network,
            "allowlist": st.get("allowlist") or "",
        }}

    @app.get("/setup/bot-qr", dependencies=[Depends(require_auth)])
    def bot_qr():
        pub = st.get("bot_public") or ""
        if not is_valid_address(pub):
            raise HTTPException(404, "No bot account is configured.")
        return {"ok": True, "public_key": pub, "network": settings.stellar_network, "svg": qr_svg(pub)}

    @app.get("/stellar/account", dependencies=[Depends(require_auth)])
    def stellar_account(address: str = ""):
        addr = (address or st.get("bot_public") or "").strip()
        if not is_valid_address(addr):
            raise HTTPException(400, "address must be a public G... address")
        info = _account(addr)
        out = {"ok": bool(info.get("ok")), "public_key": addr, "network": settings.stellar_network,
               "funded": bool(info.get("funded")), "error": info.get("error")}
        if info.get("ok") and info.get("account"):
            out.update(_balances(info["account"]))
            out["sequence"] = info["account"].get("sequence")
        return sanitize_out(out)

    @app.post("/stellar/quote", dependencies=[Depends(require_auth)])
    def stellar_quote(body: QuoteIn):
        q = _quote(body.direction, body.send_amount, body.slippage_pct)
        if q.get("ok"):
            q = {**q, "slippage_pct": body.slippage_pct,
                 "dest_min": min_received(q.get("dest_amount") or q.get("destination_amount"), body.slippage_pct)}
        return sanitize_out(q)

    def _signed_or_wallet(xdr: str, signer: str, do_submit: bool):
        net = settings.stellar_network
        if signer == "wallet":
            return {"ok": True, "signer": "wallet", "xdr": xdr, "submitted": False, "network": net,
                    "preview": not can_submit(net, settings.allow_live, st["stellar_armed"])}
        secret = st.get("bot_secret") or ""
        if not is_secret_key(secret):
            raise HTTPException(403, "No dedicated bot account is configured on this bridge.")
        signed = sign_xdr(xdr, secret, net)
        submitted = False
        result = None
        preview = not can_submit(net, settings.allow_live, st["stellar_armed"])
        if do_submit and not preview:
            res = _submit(signed)
            submitted = bool(res.get("ok"))
            result = res.get("result")
        return {"ok": True, "signer": "bot", "submitted": submitted, "preview": preview and not submitted,
                "network": net, "result": result}

    @app.post("/stellar/trustline", dependencies=[Depends(require_auth)])
    def stellar_trustline(body: TrustlineIn):
        pub = st.get("bot_public") if body.signer == "bot" else (st.get("allowlist") or "")
        if not is_valid_address(pub):
            raise HTTPException(400, "Need a bot public key or a connected wallet address.")
        info = _account(pub)
        if not info.get("ok"):
            raise HTTPException(400, "Account is not on-chain yet. Fund it with at least 1 XLM first (2+ XLM recommended).")
        seq = info["account"]["sequence"]
        xdr = build_change_trust_xdr(settings.stellar_network, pub, seq)
        if not body.confirm:
            return {"ok": True, "needs_confirm": True, "public_key": pub}
        out = _signed_or_wallet(xdr, body.signer, do_submit=True)
        out["public_key"] = pub
        return sanitize_out(out)

    @app.post("/stellar/convert", dependencies=[Depends(require_auth)])
    def stellar_convert(body: ConvertIn):
        if not body.confirm:
            q = _quote(body.direction, body.send_amount, body.slippage_pct)
            return sanitize_out({**q, "needs_confirm": True})
        pub = st.get("bot_public") if body.signer == "bot" else (st.get("allowlist") or "")
        if not is_valid_address(pub):
            raise HTTPException(400, "Need a bot public key or a connected wallet address.")
        q = _quote(body.direction, body.send_amount, body.slippage_pct)
        if not q.get("ok"):
            raise HTTPException(502, q.get("error") or "quote failed")
        info = _account(pub)
        if not info.get("ok"):
            raise HTTPException(400, "Account is not on-chain yet. Fund it first.")
        from stellar_sdk import Asset
        net = settings.stellar_network
        usdc = Asset("USDC", usdc_issuer(net))
        if body.direction == "xlm_to_usdc":
            send_asset, dest_asset = Asset.native(), usdc
        else:
            send_asset, dest_asset = usdc, Asset.native()
        dest_min = min_received(q.get("dest_amount"), body.slippage_pct)
        xdr = build_path_payment_xdr(
            net, pub, info["account"]["sequence"],
            send_asset, body.send_amount, dest_asset, dest_min, path=q.get("path") or [],
        )
        out = _signed_or_wallet(xdr, body.signer, do_submit=True)
        out.update({"quote": q, "dest_min": dest_min, "public_key": pub, "direction": body.direction})
        audit({"result": "stellar_convert", "direction": body.direction, "signer": body.signer,
               "submitted": out.get("submitted"), "preview": out.get("preview")})
        return sanitize_out(out)

    @app.post("/stellar/withdraw", dependencies=[Depends(require_auth)])
    def stellar_withdraw(body: WithdrawIn):
        if body.destination.strip() != (st.get("allowlist") or "").strip():
            raise HTTPException(403, "Withdrawals from the bot account can only go to the allowlisted wallet.")
        if not body.confirm:
            return {"ok": True, "needs_confirm": True, "destination": body.destination}
        pub = st.get("bot_public") or ""
        secret = st.get("bot_secret") or ""
        if not is_valid_address(pub) or not is_secret_key(secret):
            raise HTTPException(403, "No dedicated bot account is configured on this bridge.")
        info = _account(pub)
        if not info.get("ok"):
            raise HTTPException(400, "Bot account is not on-chain yet.")
        from stellar_sdk import Account, Asset, TransactionBuilder
        net = settings.stellar_network
        asset = Asset.native() if body.asset == "XLM" else Asset("USDC", usdc_issuer(net))
        acc = Account(pub, int(info["account"]["sequence"]))
        from bridge.stellar_dex import network_passphrase
        xdr = (TransactionBuilder(acc, network_passphrase(net), base_fee=100)
               .append_payment_op(destination=body.destination, asset=asset, amount=str(body.amount))
               .set_timeout(120).build().to_xdr())
        out = _signed_or_wallet(xdr, "bot", do_submit=True)
        out["destination"] = body.destination
        return sanitize_out(out)

    @app.post("/stellar/mode", dependencies=[Depends(require_auth)])
    def stellar_mode(body: StellarModeIn):
        if body.mode == "OFF":
            st["stellar_armed"] = False
            st["stellar_testnet_armed"] = False
            return {"ok": True, "stellar_armed": False, "stellar_testnet_armed": False, "network": settings.stellar_network}
        if body.mode == "TESTNET":
            if normalize_network(settings.stellar_network) != "testnet":
                raise HTTPException(400, "Bridge STELLAR_NETWORK is not testnet. Use LIVE on public only after arming.")
            st["stellar_testnet_armed"] = True
            st["stellar_armed"] = False
            audit({"result": "stellar_testnet_armed"})
            return {"ok": True, "stellar_armed": False, "stellar_testnet_armed": True, "network": "testnet"}
        # LIVE = public/mainnet
        if normalize_network(settings.stellar_network) != "public":
            raise HTTPException(400, "LIVE arm is for STELLAR_NETWORK=public. Use TESTNET on testnet.")
        if not settings.allow_live:
            raise HTTPException(403, "LIVE is off: set ALPHA_HIVE_ALLOW_LIVE=1 in bridge/.env and restart.")
        if body.confirm != LIVE_CONFIRM_PHRASE:
            raise HTTPException(400, f"To arm LIVE send confirm='{LIVE_CONFIRM_PHRASE}'.")
        if stellar_guard.kill_engaged():
            raise HTTPException(423, "Kill switch is engaged.")
        st["stellar_armed"] = True
        audit({"result": "stellar_live_armed"})
        return {"ok": True, "stellar_armed": True, "stellar_testnet_armed": False, "network": "public"}

    @app.post("/stellar/orders", dependencies=[Depends(require_auth)])
    def stellar_orders(body: StellarOrderIn):
        sym = body.symbol.strip().upper().replace("_", "-").replace("/", "-")
        if sym not in {"XLM", "XLM-USDC", "USDC", "USDC-XLM"}:
            raise HTTPException(400, "Live session trades XLM/USDC on the Stellar DEX only.")
        risk_status = (body.risk or {}).get("status")
        if risk_status not in ("approve", "reduce"):
            raise HTTPException(403, "Risk guard did not approve this call.")
        qcall = ((body.queen or {}).get("call") or "").upper()
        if body.side == "buy" and qcall not in {"LONG", "BUY"}:
            raise HTTPException(403, "Queen call does not match a buy.")
        if body.side == "sell" and qcall not in {"SHORT", "SELL"}:
            raise HTTPException(403, "Queen call does not match a sell.")
        if body.side == "sell":
            # Spot USDC-funded book is long-only unless the account holds XLM to sell.
            pub = st.get("bot_public") if body.signer == "bot" else (st.get("allowlist") or "")
            held = "0"
            if is_valid_address(pub):
                info = _account(pub)
                if info.get("ok"):
                    held = _balances(info["account"])["xlm"]
            try:
                xlm_held = float(held)
            except (TypeError, ValueError):
                xlm_held = 0.0
            if xlm_held <= 0:
                raise HTTPException(403, "Stellar DEX spot is long-only unless the account holds XLM to sell.")
        net = settings.stellar_network
        equity = body.balance
        if equity is None:
            equity = 0.0
        try:
            stellar_guard.pre_trade(equity, None, 0)
        except Blocked as e:
            raise HTTPException(403, str(e))
        entry = body.entry if body.entry is not None else body.entry_ref
        details = {"lotSize": 1.0, "lotStep": 0.0000001, "minLot": 0.0000001, "leverage": 1}
        try:
            plan = sizing.plan(balance=equity, side=body.side, entry=entry, stop=body.stop,
                               target=body.target, risk_pct=body.risk_pct, limits=settings.stellar_limits,
                               details=details, entry_ref=body.entry_ref)
        except Blocked as e:
            raise HTTPException(403, str(e))
        preview = not can_submit(net, settings.allow_live, st["stellar_armed"])
        if body.dry_run or preview:
            audit({"result": "stellar_preview", "plan": {"side": plan["side"], "units": plan["units"],
                                                          "notional_usd": plan["notional_usd"]}})
            return {"ok": True, "preview": True, "submitted": False, "mode": "PREVIEW",
                    "network": net, "plan": plan, "symbol": "XLM-USDC"}
        # Testnet (or armed mainnet) would build+sign+submit a path payment here. The preview
        # path is the default; live submit is gated by can_submit.
        audit({"result": "stellar_order", "plan": {"side": plan["side"], "units": plan["units"],
                                                    "notional_usd": plan["notional_usd"]}})
        return {"ok": True, "preview": False, "submitted": False, "mode": "TESTNET",
                "network": net, "plan": plan, "symbol": "XLM-USDC"}

    # expose disarm for idle expiry in require_auth
    st["_disarm"] = _disarm
    return st
