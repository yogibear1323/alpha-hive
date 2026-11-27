"""Unlock passphrase, session tokens, lockout, and Stellar quote math. Offline."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.envfile import upsert_env  # noqa: E402
from bridge.session import (  # noqa: E402
    LockedOut, MIN_PASSPHRASE_LEN, SessionStore, UnlockGuard,
    hash_passphrase, verify_passphrase,
)
from bridge.settings import load_settings  # noqa: E402
from bridge.stellar_dex import (  # noqa: E402
    can_submit, is_secret_key, is_valid_address, min_received,
    parse_strict_send_quote, sanitize_out,
)

TOKEN = "test-bridge-token-0123456789"
PASS = "correct-horse-test-phrase"


def test_hash_and_verify_passphrase():
    hashed = hash_passphrase(PASS)
    assert hashed and PASS not in hashed
    assert hashed.startswith("$argon2") or hashed.startswith("scrypt$")
    assert verify_passphrase(hashed, PASS) is True
    assert verify_passphrase(hashed, "wrong-passphrase") is False
    assert verify_passphrase(hashed, "") is False
    assert verify_passphrase("", PASS) is False


def test_short_passphrase_rejected():
    with pytest.raises(ValueError):
        hash_passphrase("short")
    with pytest.raises(ValueError):
        hash_passphrase("")


def test_session_issue_touch_and_idle_expiry():
    clock = {"t": 1000.0}

    def now():
        return clock["t"]

    store = SessionStore(clock=now)
    token, exp = store.issue(idle_seconds=30)
    assert token and exp == 1030.0
    assert store.touch(token, 30) is not None
    clock["t"] = 1020.0
    rec = store.touch(token, 30)
    assert rec is not None and rec["exp"] == 1050.0
    clock["t"] = 1051.0
    assert store.touch(token, 30) is None
    assert store.drop(token) is False


def test_session_drop():
    store = SessionStore()
    token, _ = store.issue(60)
    assert store.touch(token, 60) is not None
    assert store.drop(token) is True
    assert store.touch(token, 60) is None


def test_unlock_lockout_after_max_failures(tmp_path):
    clock = {"t": 0.0}
    g = UnlockGuard(tmp_path / "unlock.json", max_attempts=5, lockout_seconds=90, clock=lambda: clock["t"])
    for i in range(4):
        st = g.failure()
        assert st["locked"] is False
        assert st["failures"] == i + 1
    st = g.failure()
    assert st["locked"] is True and st["retry_after"] == 90
    with pytest.raises(LockedOut) as ei:
        g.assert_not_locked()
    assert ei.value.retry_after == 90
    clock["t"] = 89
    assert g.status()["locked"] is True
    clock["t"] = 90
    assert g.status()["locked"] is False
    g.success()
    assert g.status()["failures"] == 0


def test_unlock_success_resets_failures(tmp_path):
    g = UnlockGuard(tmp_path / "unlock.json", max_attempts=5, lockout_seconds=90)
    g.failure(); g.failure()
    g.success()
    assert g.status()["failures"] == 0 and g.status()["locked"] is False


def test_envfile_upsert_does_not_duplicate(tmp_path):
    path = tmp_path / ".env"
    path.write_text("FOO=1\nBAR=2\n", encoding="utf-8")
    upsert_env(path, "ALPHA_HIVE_PASSPHRASE_HASH", "hash-one")
    upsert_env(path, "ALPHA_HIVE_PASSPHRASE_HASH", "hash-two")
    text = path.read_text(encoding="utf-8")
    assert text.count("ALPHA_HIVE_PASSPHRASE_HASH=") == 1
    assert "hash-two" in text and "hash-one" not in text
    assert "FOO=1" in text and "BAR=2" in text


def test_settings_hides_passphrase_hash_and_bot_secret():
    hashed = hash_passphrase(PASS)
    s = load_settings({
        "BRIDGE_TOKEN": TOKEN,
        "ALPHA_HIVE_PASSPHRASE_HASH": hashed,
        "STELLAR_BOT_SECRET": "SABCDEFGHIJKLMNOPQRSTUVWXYZ234567ABCDEFGHIJKLMNOPQRSTUVW",
    })
    r = repr(s)
    assert hashed not in r
    assert "SABCDEFGHIJKLMNOPQRSTUVWXYZ234567" not in r
    assert s.has_passphrase is True
    assert s.stellar_network == "testnet"
    assert s.stellar_limits.max_position_usd == 25.0
    assert s.stellar_limits.daily_loss_stop_usd == 5.0


def test_min_received_floors_and_never_rounds_up():
    assert min_received("2.0000000", "0.5") == "1.99"
    assert min_received("8.0000000", "0.5") == "7.96"
    # 1.0000001 * 0.995 must floor, not round up
    assert min_received("1.0000001", "0.5") is not None
    v = min_received("1.0000001", "0.5")
    from bridge.stellar_dex import to_stroops
    assert to_stroops(v) <= int(1.0000001 * 10_000_000 * 0.995)


def test_parse_strict_send_quote_picks_best_path():
    body = {"_embedded": {"records": [
        {"source_amount": "8", "destination_amount": "1.5", "destination_asset_code": "USDC",
         "destination_asset_issuer": "G" + "B" * 55, "path": []},
        {"source_amount": "8", "destination_amount": "2.00", "destination_asset_code": "USDC",
         "destination_asset_issuer": "G" + "C" * 55, "path": []},
    ]}}
    q = parse_strict_send_quote(body, "0.5")
    assert q["ok"] is True
    assert q["dest_amount"] == "2.00"
    assert q["dest_min"] == "1.99"


def test_can_submit_testnet_vs_mainnet():
    assert can_submit("testnet", False, False) is True
    assert can_submit("public", False, False) is False
    assert can_submit("public", True, False) is False
    assert can_submit("public", True, True) is True
    assert can_submit("mainnet", True, True) is True


def test_sanitize_out_drops_secrets():
    secret = "SABCDEFGHIJKLMNOPQRSTUVWXYZ234567ABCDEFGHIJKLMNOPQRSTUVW"
    out = sanitize_out({"public_key": "G" + "A" * 55, "secret": secret, "bot_secret": secret, "ok": True})
    assert "secret" not in out and "bot_secret" not in out
    assert out["ok"] is True
    assert not any(isinstance(v, str) and is_secret_key(v) for v in out.values())
    assert is_valid_address("G" + "A" * 55)


def test_wallet_challenge_roundtrip_issues_nothing_until_verified():
    from stellar_sdk import Keypair
    from bridge.session import ChallengeStore, sign_unlock_message
    kp = Keypair.random()
    store = ChallengeStore(ttl_seconds=60)
    ch = store.issue(kp.public_key, "testnet")
    assert ch["method"] == "wallet"
    assert ch["address"] == kp.public_key
    assert "ALPHA-HIVE-UNLOCK:testnet:" in ch["message"]
    sig = sign_unlock_message(kp.secret, ch["message"])
    rec = store.consume(ch["challenge_id"], kp.public_key, sig)
    assert rec["address"] == kp.public_key
    # consumed: cannot reuse
    with pytest.raises(ValueError):
        store.consume(ch["challenge_id"], kp.public_key, sig)


def test_wallet_challenge_rejects_wrong_signature_and_expired():
    from stellar_sdk import Keypair
    from bridge.session import ChallengeStore, sign_unlock_message
    kp = Keypair.random()
    other = Keypair.random()
    clock = {"t": 0.0}
    store = ChallengeStore(clock=lambda: clock["t"], ttl_seconds=30)
    ch = store.issue(kp.public_key, "testnet")
    bad = sign_unlock_message(other.secret, ch["message"])
    with pytest.raises(ValueError):
        store.consume(ch["challenge_id"], kp.public_key, bad)
    clock["t"] = 31
    ch2 = store.issue(kp.public_key, "testnet")
    # the first challenge is expired
    with pytest.raises(ValueError):
        sig = sign_unlock_message(kp.secret, ch["message"])
        store.consume(ch["challenge_id"], kp.public_key, sig)
    # a fresh one still works
    sig = sign_unlock_message(kp.secret, ch2["message"])
    # ch2 also expired because clock is 31 and it was issued at 31 with ttl 30? issued at 31, exp 61
    store.consume(ch2["challenge_id"], kp.public_key, sig)


def test_session_records_unlock_method():
    store = SessionStore()
    token, _ = store.issue(60, method="wallet")
    rec = store.touch(token, 60)
    assert rec["method"] == "wallet"
