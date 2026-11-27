"""Unlock, session auth, Fund-panel helpers, and Stellar live-session gates. Offline / mocked Horizon."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stellar_sdk import Keypair

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.server import create_app  # noqa: E402
from bridge.session import hash_passphrase, sign_unlock_message  # noqa: E402
from bridge.settings import LIVE_CONFIRM_PHRASE, load_settings  # noqa: E402
from bridge.stellar_dex import is_secret_key  # noqa: E402

TOKEN = "test-bridge-token-0123456789"
PASS = "correct-horse-test-phrase"


def make(tmp_path, **env):
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"BRIDGE_TOKEN={TOKEN}\n", encoding="utf-8")
    base = {
        "BRIDGE_TOKEN": TOKEN,
        "BRIDGE_STATE_DIR": str(tmp_path / "state"),
        "TRADELOCKER_EMAIL": "robert@example.com",
        "TRADELOCKER_PASSWORD": "x",
        "TRADELOCKER_SERVER": "demo",
    }
    s = load_settings({**base, **env}, dotenv_path=dotenv)
    app = create_app(s)
    return TestClient(app), s, dotenv


def test_setup_status_and_health_are_public(tmp_path):
    c, s, _ = make(tmp_path)
    h = c.get("/health")
    assert h.status_code == 200 and h.json()["ok"] is True
    st = c.get("/setup/status")
    assert st.status_code == 200
    body = st.json()
    assert body["ok"] is True
    assert body["passphrase_set"] is False
    assert "wallet" in body["unlock_methods"]
    assert body["bot_account"] is None
    assert "secret" not in str(body).lower() or "secret" not in body


def test_existing_bridge_token_still_unlocks_status(tmp_path):
    c, _, _ = make(tmp_path)
    r = c.get("/status", headers={"X-Bridge-Token": TOKEN})
    assert r.status_code == 200
    assert c.get("/status").status_code == 401


def test_passphrase_setup_unlock_and_session(tmp_path):
    c, _, dotenv = make(tmp_path)
    bad = c.post("/setup/passphrase", json={"passphrase": "short"})
    assert bad.status_code == 400
    r = c.post("/setup/passphrase", json={"passphrase": PASS})
    assert r.status_code == 200 and r.json()["passphrase_set"] is True
    assert "passphrase" in r.json()["unlock_methods"]
    text = dotenv.read_text(encoding="utf-8")
    assert "ALPHA_HIVE_PASSPHRASE_HASH=" in text
    assert PASS not in text
    # already set
    assert c.post("/setup/passphrase", json={"passphrase": PASS}).status_code == 409
    # wrong passphrase
    assert c.post("/unlock", json={"passphrase": "wrong-passphrase-xx"}).status_code == 401
    ok = c.post("/unlock", json={"passphrase": PASS})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] and body["method"] == "passphrase" and body["token"]
    tok = body["token"]
    sess = c.get("/session", headers={"X-Session-Token": tok})
    assert sess.status_code == 200 and sess.json()["unlocked"] is True
    # token value is one-time in the unlock body; not echoed later
    assert tok not in sess.text


def test_unlock_lockout_then_logout_disarms(tmp_path):
    hashed = hash_passphrase(PASS)
    c, s, _ = make(tmp_path, ALPHA_HIVE_PASSPHRASE_HASH=hashed, ALPHA_HIVE_UNLOCK_MAX_ATTEMPTS="5",
                   ALPHA_HIVE_UNLOCK_LOCKOUT_SECONDS="90")
    # put a live arm in state via CLI token, then logout
    # first unlock correctly after failures
    for _ in range(4):
        assert c.post("/unlock", json={"passphrase": "nope-nope-nope"}).status_code == 401
    locked = c.post("/unlock", json={"passphrase": "nope-nope-nope"})
    assert locked.status_code == 429
    # even the right passphrase is blocked during lockout
    assert c.post("/unlock", json={"passphrase": PASS}).status_code == 429


def test_wallet_signature_unlock_issues_same_session(tmp_path):
    c, _, dotenv = make(tmp_path)
    kp = Keypair.random()
    ch = c.post("/unlock/challenge", json={"address": kp.public_key})
    assert ch.status_code == 200
    body = ch.json()
    assert body["method"] == "wallet" and body["message"].startswith("ALPHA-HIVE-UNLOCK:")
    sig = sign_unlock_message(kp.secret, body["message"])
    # wrong sig
    other = Keypair.random()
    bad = sign_unlock_message(other.secret, body["message"])
    assert c.post("/unlock/wallet", json={
        "address": kp.public_key, "challenge_id": body["challenge_id"], "signature": bad,
    }).status_code == 401
    ok = c.post("/unlock/wallet", json={
        "address": kp.public_key, "challenge_id": body["challenge_id"], "signature": sig,
    })
    assert ok.status_code == 200
    tok = ok.json()["token"]
    assert ok.json()["method"] == "wallet"
    sess = c.get("/session", headers={"X-Session-Token": tok})
    assert sess.status_code == 200
    # allowlist recorded, no secret in env
    text = dotenv.read_text(encoding="utf-8")
    assert kp.public_key in text
    assert "STELLAR_WITHDRAW_ALLOWLIST=" in text
    assert kp.secret not in text


def test_bot_account_returns_public_key_only(tmp_path):
    c, _, dotenv = make(tmp_path)
    hashed = hash_passphrase(PASS)
    # reload with hash? easier: set via API then unlock
    c2, _, dotenv = make(tmp_path)
    c2.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c2.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h = {"X-Session-Token": tok}
    r = c2.post("/setup/bot-account", json={"allowlist": ""}, headers=h)
    assert r.status_code == 200 and r.json()["created"] is True
    pub = r.json()["public_key"]
    assert pub.startswith("G") and len(pub) == 56
    assert not any(is_secret_key(str(v)) for v in r.json().values())
    text = dotenv.read_text(encoding="utf-8")
    assert f"STELLAR_BOT_PUBLIC_KEY={pub}" in text
    # secret is in the file but never in the HTTP body
    secret_line = [ln for ln in text.splitlines() if ln.startswith("STELLAR_BOT_SECRET=")]
    assert len(secret_line) == 1 and secret_line[0].split("=", 1)[1].startswith("S")
    assert secret_line[0].split("=", 1)[1] not in r.text
    qr = c2.get("/setup/bot-qr", headers=h)
    assert qr.status_code == 200 and "<svg" in qr.json()["svg"] and qr.json()["public_key"] == pub
    # second call does not rotate
    again = c2.post("/setup/bot-account", json={}, headers=h)
    assert again.json()["created"] is False and again.json()["public_key"] == pub


def test_logout_disarms_stellar_and_broker_live(tmp_path):
    c, _, _ = make(tmp_path)
    c.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h = {"X-Session-Token": tok}
    # testnet arm
    m = c.post("/stellar/mode", json={"mode": "TESTNET"}, headers=h)
    assert m.status_code == 200 and m.json()["stellar_testnet_armed"] is True
    out = c.post("/logout", headers=h)
    assert out.status_code == 200
    assert out.json()["live_armed"] is False and out.json()["stellar_armed"] is False
    assert c.get("/session", headers=h).status_code == 401


def test_stellar_mainnet_order_is_preview_without_arm(tmp_path):
    c, _, _ = make(tmp_path, STELLAR_NETWORK="public", ALPHA_HIVE_ALLOW_LIVE="0")
    c.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h = {"X-Session-Token": tok}
    # LIVE arm refused without flag
    arm = c.post("/stellar/mode", json={"mode": "LIVE", "confirm": LIVE_CONFIRM_PHRASE}, headers=h)
    assert arm.status_code == 403
    r = c.post("/stellar/orders", json={
        "symbol": "XLM-USDC", "side": "buy", "entry": 0.25, "entry_ref": 0.25,
        "stop": 0.24, "target": 0.26, "risk_pct": 0.5, "balance": 100,
        "risk": {"status": "approve"}, "queen": {"call": "LONG"},
    }, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["preview"] is True and body["submitted"] is False
    assert body["plan"]["notional_usd"] <= 25.0


def test_stellar_orders_require_risk_and_reject_unrelated_pairs(tmp_path):
    c, _, _ = make(tmp_path)
    c.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h = {"X-Session-Token": tok}
    bad_sym = c.post("/stellar/orders", json={
        "symbol": "BTC-USD", "side": "buy", "entry": 100, "stop": 90, "target": 110,
        "balance": 100, "risk": {"status": "approve"}, "queen": {"call": "LONG"},
    }, headers=h)
    assert bad_sym.status_code == 400
    rejected = c.post("/stellar/orders", json={
        "symbol": "XLM-USDC", "side": "buy", "entry": 0.25, "stop": 0.24, "target": 0.26,
        "balance": 100, "risk": {"status": "reject"}, "queen": {"call": "LONG"},
    }, headers=h)
    assert rejected.status_code == 403


def test_stellar_kill_switch_and_daily_loss_block_orders(tmp_path):
    c, s, _ = make(tmp_path, STELLAR_DAILY_LOSS_STOP_USD="5")
    c.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h = {"X-Session-Token": tok}
    # engage kill via shared kill file
    s.state_dir.mkdir(parents=True, exist_ok=True)
    (s.state_dir / "KILL_SWITCH").write_text("{}", encoding="utf-8")
    killed = c.post("/stellar/orders", json={
        "symbol": "XLM-USDC", "side": "buy", "entry": 0.25, "entry_ref": 0.25,
        "stop": 0.24, "target": 0.26, "risk_pct": 0.5, "balance": 100,
        "risk": {"status": "approve"}, "queen": {"call": "LONG"},
    }, headers=h)
    assert killed.status_code == 403
    assert "Kill switch" in killed.json()["detail"]
    (s.state_dir / "KILL_SWITCH").unlink()
    # daily loss latch: start equity 100, already down $6
    import json
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (s.state_dir / "daily_stellar.json").write_text(json.dumps({
        "day": day, "start_equity": 100.0, "stopped": True, "loss_usd": 6.0,
        "loss_pct": 6.0, "limit_pct": 2.0, "limit_usd": 5.0,
    }), encoding="utf-8")
    stopped = c.post("/stellar/orders", json={
        "symbol": "XLM-USDC", "side": "buy", "entry": 0.25, "entry_ref": 0.25,
        "stop": 0.24, "target": 0.26, "risk_pct": 0.5, "balance": 94,
        "risk": {"status": "approve"}, "queen": {"call": "LONG"},
    }, headers=h)
    assert stopped.status_code == 403
    assert "Daily loss" in stopped.json()["detail"]


def test_withdraw_must_match_allowlist(tmp_path):
    c, _, _ = make(tmp_path)
    c.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h = {"X-Session-Token": tok}
    wallet = Keypair.random().public_key
    other = Keypair.random().public_key
    c.post("/setup/wallet", json={"address": wallet}, headers=h)
    c.post("/setup/bot-account", json={"allowlist": wallet}, headers=h)
    r = c.post("/stellar/withdraw", json={
        "destination": other, "amount": "1", "asset": "XLM", "confirm": True,
    }, headers=h)
    assert r.status_code == 403


def test_convert_preview_on_public_does_not_submit(tmp_path):
    c, s, _ = make(tmp_path, STELLAR_NETWORK="public", ALPHA_HIVE_ALLOW_LIVE="0")
    c.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h = {"X-Session-Token": tok}
    c.post("/setup/bot-account", json={}, headers=h)
    pub = c.get("/setup/bot-account", headers=h).json()["bot_account"]["public_key"]
    submitted = {"n": 0}

    def quote_fn(direction, amount, slip):
        return {"ok": True, "dest_amount": "2.00", "source_amount": amount, "path": []}

    def account_fn(address):
        return {"ok": True, "funded": True, "public_key": address,
                "account": {"sequence": "1", "balances": [{"asset_type": "native", "balance": "10.0"}]}}

    class Boom:
        def submit_transaction(self, xdr):
            submitted["n"] += 1
            raise AssertionError("must not submit on public without allow_live + arm")

    s_app = c.app
    st = s_app.state.bridge
    st["quote_fn"] = quote_fn
    st["account_fn"] = account_fn
    st["submitter"] = Boom()
    r = c.post("/stellar/convert", json={
        "direction": "xlm_to_usdc", "send_amount": "8", "slippage_pct": 0.5,
        "confirm": True, "signer": "bot",
    }, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["preview"] is True and body["submitted"] is False
    assert submitted["n"] == 0
    assert "secret" not in body and not any(isinstance(v, str) and v.startswith("S") and len(v) == 56 for v in body.values())
    assert body.get("public_key") == pub


def test_unlock_response_and_status_never_leak_secrets(tmp_path):
    c, _, dotenv = make(tmp_path)
    c.post("/setup/passphrase", json={"passphrase": PASS})
    tok = c.post("/unlock", json={"passphrase": PASS}).json()["token"]
    h = {"X-Session-Token": tok}
    c.post("/setup/bot-account", json={}, headers=h)
    secret = [ln.split("=", 1)[1] for ln in dotenv.read_text().splitlines() if ln.startswith("STELLAR_BOT_SECRET=")][0]
    hashed = [ln.split("=", 1)[1] for ln in dotenv.read_text().splitlines() if ln.startswith("ALPHA_HIVE_PASSPHRASE_HASH=")][0]
    for path, method, json_body in (
        ("/setup/status", "GET", None),
        ("/session", "GET", None),
        ("/setup/bot-account", "GET", None),
        ("/setup/bot-qr", "GET", None),
    ):
        r = c.request(method, path, headers=h)
        assert r.status_code == 200, path
        assert secret not in r.text
        assert hashed not in r.text
        assert PASS not in r.text
