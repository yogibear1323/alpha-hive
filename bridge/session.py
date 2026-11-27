"""Unlock authenticators and short-lived session tokens.

Passphrase hashing is one authenticator. Wallet-signature unlock (SEP-10 style:
the bridge issues a short-lived challenge, the wallet signs it, the bridge verifies
the public G address) issues the same kind of session. Either method, or both, can
be enabled. Nothing in this module logs a passphrase, hash, challenge, signature, or session token.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
    _HASHER = PasswordHasher()
    _HAS_ARGON2 = True
except ImportError:  # pragma: no cover
    _HASHER = None
    _HAS_ARGON2 = False

MIN_PASSPHRASE_LEN = 8


def hash_passphrase(passphrase: str) -> str:
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_LEN:
        raise ValueError(f"passphrase must be at least {MIN_PASSPHRASE_LEN} characters")
    if _HAS_ARGON2:
        return _HASHER.hash(passphrase)
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_passphrase(stored: str, passphrase: str) -> bool:
    if not stored or not isinstance(passphrase, str):
        return False
    if stored.startswith("scrypt$"):
        try:
            _, n, r, p, salt_hex, dk_hex = stored.split("$")
            dk = hashlib.scrypt(
                passphrase.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(dk_hex)),
            )
            return hmac.compare_digest(dk, bytes.fromhex(dk_hex))
        except (ValueError, TypeError):
            return False
    if not _HAS_ARGON2:
        return False
    try:
        return bool(_HASHER.verify(stored, passphrase))
    except (VerifyMismatchError, VerificationError, InvalidHash, ValueError, TypeError):
        return False


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionStore:
    """In-memory sessions, keyed by SHA-256 of the token. Lost on process restart."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._items: dict[str, dict] = {}

    def issue(self, idle_seconds: int, method: str = "passphrase") -> tuple[str, float]:
        token = secrets.token_urlsafe(32)
        now = float(self.clock())
        exp = now + max(1, int(idle_seconds))
        self._items[_token_key(token)] = {"created": now, "last": now, "exp": exp, "method": method}
        return token, exp

    def touch(self, token: str, idle_seconds: int) -> dict | None:
        if not token:
            return None
        rec = self._items.get(_token_key(token))
        if not rec:
            return None
        now = float(self.clock())
        if now > rec["exp"]:
            self._items.pop(_token_key(token), None)
            return None
        rec["last"] = now
        rec["exp"] = now + max(1, int(idle_seconds))
        return rec

    def drop(self, token: str) -> bool:
        if not token:
            return False
        return self._items.pop(_token_key(token), None) is not None

    def drop_all(self) -> None:
        self._items.clear()

    def has_any(self) -> bool:
        now = float(self.clock())
        for k, v in list(self._items.items()):
            if now > v["exp"]:
                self._items.pop(k, None)
        return bool(self._items)

    def expired_present(self) -> bool:
        now = float(self.clock())
        dead = [k for k, v in self._items.items() if now > v["exp"]]
        for k in dead:
            self._items.pop(k, None)
        return bool(dead) and not self._items


class UnlockGuard:
    """Counts failed unlocks and enforces a lockout. State is a small JSON file (no secrets)."""

    def __init__(self, path: Path, max_attempts: int = 5, lockout_seconds: int = 900, clock=time.time):
        self.path = Path(path)
        self.max_attempts = max(1, int(max_attempts))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self.clock = clock

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        return d if isinstance(d, dict) else {}

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(self.path)

    def status(self) -> dict:
        d = self._load()
        until = float(d.get("until") or 0)
        now = float(self.clock())
        if until and now >= until:
            d = {"failures": 0, "until": 0}
            self._save(d)
            until = 0
        locked = bool(until and now < until)
        return {
            "locked": locked,
            "failures": int(d.get("failures") or 0),
            "retry_after": max(0, int(until - now)) if locked else 0,
        }

    def assert_not_locked(self) -> None:
        st = self.status()
        if st["locked"]:
            raise LockedOut(st["retry_after"])

    def success(self) -> None:
        self._save({"failures": 0, "until": 0})

    def failure(self) -> dict:
        d = self._load()
        now = float(self.clock())
        until = float(d.get("until") or 0)
        if until and now < until:
            return self.status()
        fails = int(d.get("failures") or 0) + 1
        if fails >= self.max_attempts:
            d = {"failures": fails, "until": now + self.lockout_seconds}
        else:
            d = {"failures": fails, "until": 0}
        self._save(d)
        return self.status()


class LockedOut(Exception):
    def __init__(self, retry_after: int):
        super().__init__("too many failed unlocks")
        self.retry_after = retry_after


class ChallengeStore:
    """Short-lived wallet-unlock challenges. Same session is issued after a verified signature.

    The current challenge is a domain-separated message the Stellar keypair signs.
    A full SEP-10 challenge transaction can replace `message` later without changing
    session issuance: verify the signed payload, then call SessionStore.issue(..., method="wallet").
    """

    def __init__(self, clock=time.time, ttl_seconds: int = 120):
        self.clock = clock
        self.ttl_seconds = max(30, int(ttl_seconds))
        self._items: dict[str, dict] = {}

    def _purge(self) -> None:
        now = float(self.clock())
        for k, v in list(self._items.items()):
            if now > v["exp"]:
                self._items.pop(k, None)

    def issue(self, address: str, network: str = "testnet") -> dict:
        from bridge.stellar_dex import is_valid_address, normalize_network
        if not is_valid_address(address):
            raise ValueError("challenge address must be a public G... address")
        self._purge()
        cid = secrets.token_urlsafe(16)
        now = float(self.clock())
        exp = now + self.ttl_seconds
        nonce = secrets.token_hex(32)
        net = normalize_network(network)
        addr = address.strip()
        message = f"ALPHA-HIVE-UNLOCK:{net}:{addr}:{nonce}:{int(exp)}"
        self._items[cid] = {"address": addr, "network": net, "nonce": nonce, "message": message, "exp": exp}
        return {
            "challenge_id": cid,
            "address": addr,
            "network": net,
            "message": message,
            "expires_in": self.ttl_seconds,
            "method": "wallet",
        }

    def peek(self, challenge_id: str) -> dict | None:
        self._purge()
        return self._items.get(challenge_id)

    def consume(self, challenge_id: str, address: str, signature: str) -> dict:
        """Verify and consume a challenge. Raises ValueError on failure. Never logs the signature."""
        rec = self.peek(challenge_id)
        if not rec:
            raise ValueError("challenge expired or unknown")
        if address.strip() != rec["address"]:
            raise ValueError("address does not match challenge")
        if not _verify_stellar_sig(rec["address"], rec["message"], signature):
            raise ValueError("bad signature")
        self._items.pop(challenge_id, None)
        return {"address": rec["address"], "network": rec["network"]}


def _verify_stellar_sig(address: str, message: str, signature: str) -> bool:
    if not address or not message or not signature:
        return False
    try:
        import base64
        from stellar_sdk import Keypair
        sig = base64.b64decode(signature)
        Keypair.from_public_key(address).verify(message.encode("utf-8"), sig)
        return True
    except Exception:
        return False


def sign_unlock_message(secret: str, message: str) -> str:
    """Test helper: sign a challenge message. Not a route. Never log the secret or signature."""
    import base64
    from stellar_sdk import Keypair
    from bridge.stellar_dex import is_secret_key
    if not is_secret_key(secret):
        raise ValueError("invalid signing key")
    sig = Keypair.from_secret(secret).sign(message.encode("utf-8"))
    return base64.b64encode(sig).decode("ascii")
