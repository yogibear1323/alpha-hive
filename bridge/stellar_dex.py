"""Stellar DEX helpers: XLM/USDC quotes, transaction building, bot-account signing.

Default network is testnet. Mainnet submissions are refused unless the caller
explicitly passes allow_live and armed. Secrets are never included in returned dicts.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_DOWN

import httpx

STROOPS = 10_000_000
SLIPPAGE_PCT = Decimal("0.5")
USDC_ISSUER = {
    "public": "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN",
    "testnet": "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5",
}
HORIZON = {
    "public": "https://horizon.stellar.org",
    "testnet": "https://horizon-testnet.stellar.org",
}
PASSPHRASE = {
    "public": "Public Global Stellar Network ; September 2015",
    "testnet": "Test SDF Network ; September 2015",
}
G_ADDR = re.compile(r"^G[A-Z2-7]{55}$")
S_ADDR = re.compile(r"^S[A-Z2-7]{55}$")


def normalize_network(network: str | None) -> str:
    n = (network or "testnet").strip().lower()
    if n in ("public", "mainnet", "live"):
        return "public"
    if n in ("testnet", "test"):
        return "testnet"
    raise ValueError("network must be 'testnet' or 'public'")


def is_valid_address(addr: str | None) -> bool:
    return bool(addr and G_ADDR.fullmatch(addr.strip()))


def is_secret_key(val: str | None) -> bool:
    return bool(val and S_ADDR.fullmatch(val.strip()))


def usdc_issuer(network: str) -> str:
    return USDC_ISSUER[normalize_network(network)]


def horizon_url(network: str) -> str:
    return HORIZON[normalize_network(network)]


def network_passphrase(network: str) -> str:
    return PASSPHRASE[normalize_network(network)]


def to_stroops(amount) -> int | None:
    if amount is None or amount == "":  # typo will fail - fix
        return None
    s = str(amount).strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    sign = -1 if s.startswith("-") else 1
    if s.startswith("-"):
        s = s[1:]
    whole, _, frac = s.partition(".")
    frac = (frac + "0000000")[:7]
    try:
        return sign * (int(whole or "0") * STROOPS + int(frac))
    except ValueError:
        return None


def from_stroops(n: int) -> str:
    neg = n < 0
    n = abs(int(n))
    whole, frac = divmod(n, STROOPS)
    frac_s = f"{frac:07d}".rstrip("0")
    s = str(whole) + (("." + frac_s) if frac_s else "")
    return "-" + s if neg else s


def min_received(dest_amount, slippage_pct=SLIPPAGE_PCT) -> str | None:
    """Floor dest * (1 - slip/100) to 7 decimals. Never rounds up."""
    dest = to_stroops(dest_amount)
    slip = to_stroops(slippage_pct)
    if dest is None or dest <= 0 or slip is None or slip < 0:
        return None
    if slip >= 100 * STROOPS:
        return None
    keep = STROOPS - slip // 100
    if keep < 0:
        return None
    return from_stroops((dest * keep) // STROOPS)


def parse_strict_send_quote(body: dict, slippage_pct=SLIPPAGE_PCT) -> dict:
    records = []
    if isinstance(body, dict):
        records = (body.get("_embedded") or {}).get("records") or []
    elif isinstance(body, list):
        records = body
    best, best_dest = None, None
    for r in records:
        dest = to_stroops((r or {}).get("destination_amount"))
        if dest is None:
            continue
        if best_dest is None or dest > best_dest:
            best, best_dest = r, dest
    if not best:
        return {"ok": False, "error": "no_path", "records": len(records)}
    dest_min = min_received(best.get("destination_amount"), slippage_pct)
    path = best.get("path") or []
    codes = ["XLM"]
    for p in path:
        codes.append("XLM" if p.get("asset_type") == "native" else (p.get("asset_code") or "?"))
    codes.append(best.get("destination_asset_code") or "USDC")
    return {
        "ok": True,
        "send_amount": best.get("source_amount"),
        "dest_amount": best.get("destination_amount"),
        "dest_min": dest_min,
        "slippage_pct": float(slippage_pct),
        "path": path,
        "route": " -> ".join(codes),
        "dest_code": best.get("destination_asset_code") or "USDC",
        "dest_issuer": best.get("destination_asset_issuer"),
    }


def can_submit(network: str, allow_live: bool, live_armed: bool) -> bool:
    net = normalize_network(network)
    if net == "testnet":
        return True
    return bool(allow_live and live_armed)


def quote_xlm_to_usdc(network: str, send_amount, timeout=8.0, client: httpx.Client | None = None) -> dict:
    net = normalize_network(network)
    amount = from_stroops(to_stroops(send_amount) or 0)
    if not amount or amount.startswith("-") or amount == "0":
        return {"ok": False, "error": "amount"}
    issuer = usdc_issuer(net)
    url = (
        f"{horizon_url(net)}/paths/strict-send?source_asset_type=native"
        f"&source_amount={amount}&destination_assets=USDC:{issuer}"
    )
    http = client or httpx.Client(timeout=timeout)
    try:
        r = http.get(url, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            return {"ok": False, "error": "http", "status": r.status_code}
        return parse_strict_send_quote(r.json())
    except httpx.HTTPError:
        return {"ok": False, "error": "network"}
    finally:
        if client is None:
            http.close()


def quote_usdc_to_xlm(network: str, send_amount, timeout=8.0, client: httpx.Client | None = None) -> dict:
    net = normalize_network(network)
    amount = from_stroops(to_stroops(send_amount) or 0)
    if not amount or amount.startswith("-") or amount == "0":
        return {"ok": False, "error": "amount"}
    issuer = usdc_issuer(net)
    url = (
        f"{horizon_url(net)}/paths/strict-send?source_asset_type=credit_alphanum4"
        f"&source_asset_code=USDC&source_asset_issuer={issuer}"
        f"&source_amount={amount}&destination_assets=native"
    )
    http = client or httpx.Client(timeout=timeout)
    try:
        r = http.get(url, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            return {"ok": False, "error": "http", "status": r.status_code}
        q = parse_strict_send_quote(r.json())
        if q.get("ok"):
            q["route"] = q.get("route", "").replace("XLM ->", "USDC ->", 1) if q.get("route", "").startswith("XLM") else q.get("route")
            # parse_strict_send_quote always prefixes XLM as send; fix labels
            q["route"] = "USDC -> " + " -> ".join((q.get("route") or "").split(" -> ")[1:] or ["XLM"])
            q["dest_code"] = "XLM"
        return q
    except httpx.HTTPError:
        return {"ok": False, "error": "network"}
    finally:
        if client is None:
            http.close()


def fetch_account(network: str, address: str, timeout=8.0, client: httpx.Client | None = None) -> dict:
    if not is_valid_address(address):
        return {"ok": False, "error": "invalid_address"}
    net = normalize_network(network)
    url = f"{horizon_url(net)}/accounts/{address.strip()}"
    http = client or httpx.Client(timeout=timeout)
    try:
        r = http.get(url, headers={"Accept": "application/json"})
        if r.status_code == 404:
            return {"ok": False, "error": "not_found", "funded": False, "public_key": address.strip()}
        if r.status_code >= 400:
            return {"ok": False, "error": "http", "status": r.status_code}
        body = r.json()
        return {"ok": True, "funded": True, "account": body, "public_key": address.strip()}
    except httpx.HTTPError:
        return {"ok": False, "error": "network"}
    finally:
        if client is None:
            http.close()


def _path_assets(path, sdk):
    assets = []
    for p in path or []:
        if p.get("asset_type") == "native":
            assets.append(sdk.Asset.native())
        else:
            assets.append(sdk.Asset(p.get("asset_code"), p.get("asset_issuer")))
    return assets


def build_change_trust_xdr(network: str, source_public: str, account_sequence, fee: int = 100) -> str:
    from stellar_sdk import Asset, Network, TransactionBuilder
    net = normalize_network(network)
    from stellar_sdk import Account
    acc = Account(source_public, int(account_sequence))
    usdc = Asset("USDC", usdc_issuer(net))
    tx = (
        TransactionBuilder(acc, network_passphrase(net), base_fee=fee)
        .append_change_trust_op(usdc)
        .set_timeout(120)
        .build()
    )
    return tx.to_xdr()


def build_path_payment_xdr(network: str, source_public: str, account_sequence,
                           send_asset, send_amount, dest_asset, dest_min,
                           path=None, fee: int = 100) -> str:
    from stellar_sdk import Account, TransactionBuilder
    net = normalize_network(network)
    acc = Account(source_public, int(account_sequence))
    builder = TransactionBuilder(acc, network_passphrase(net), base_fee=fee)
    builder.append_path_payment_strict_send_op(
        destination=source_public,
        send_asset=send_asset,
        send_amount=str(send_amount),
        dest_asset=dest_asset,
        dest_min=str(dest_min),
        path=_path_assets(path, __import__("stellar_sdk")),
    )
    return builder.set_timeout(120).build().to_xdr()


def sign_xdr(xdr: str, secret: str, network: str) -> str:
    if not is_secret_key(secret):
        raise ValueError("invalid signing key")
    from stellar_sdk import Keypair, Network, TransactionEnvelope
    te = TransactionEnvelope.from_xdr(xdr, network_passphrase(network))
    te.sign(Keypair.from_secret(secret))
    return te.to_xdr()


def submit_xdr(network: str, xdr: str, timeout=30.0, client=None) -> dict:
    """Submit a signed transaction. `client` may be a stellar_sdk.Server (tests inject a fake)."""
    net = normalize_network(network)
    if client is not None and hasattr(client, "submit_transaction"):
        return {"ok": True, "result": client.submit_transaction(xdr), "network": net}
    from stellar_sdk import Server
    server = Server(horizon_url(net))
    res = server.submit_transaction(xdr)
    return {"ok": True, "result": res, "network": net}


def qr_svg(data: str) -> str:
    """Return an SVG QR for a public address. No network."""
    import segno
    qr = segno.make(data, error="m")
    return qr.svg_inline(dark="#1a1206", light="#f4ead4", scale=4, border=2)


def sanitize_out(d: dict) -> dict:
    """Drop any key that looks like a secret before a response is built."""
    out = {}
    for k, v in d.items():
        kl = k.lower()
        if "secret" in kl or kl.endswith("_key") and "public" not in kl:
            continue
        if isinstance(v, str) and is_secret_key(v):
            continue
        out[k] = v
    return out
