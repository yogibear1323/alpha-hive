"""Scan terminal pages and wallet JS for unsafe Stellar secret-key handling.

A Stellar secret key starts with S. The only allowed key field is the
optional in-memory Alpha Hive account input (id=hiveAccountSecret). It must
never be written to web storage, cookies, a URL, or a cross-origin message.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    ROOT / "web" / "templates" / "index.html",
    ROOT / "terminal" / "index.html",
    ROOT / "web" / "static-demo.html",
]
OUR_JS = ROOT / "terminal" / "vendor" / "stellar-wallet.js"
KEY_SESSION = ROOT / "terminal" / "vendor" / "stellar-key-session.js"
ALLOWED_SECRET_IDS = {"hiveAccountSecret"}

SECRET_ATTR = re.compile(
    r"(secret[_\s-]*key|secret[_\s-]*seed|stellar[_\s-]*secret|private[_\s-]*key|"
    r"secret[_\s-]*phrase|seed[_\s-]*phrase|spending[_\s-]*key)",
    re.I,
)
SECRET_PLACEHOLDER = re.compile(
    r"""placeholder\s*=\s*(['"])\s*S[A-Z0-9.]{0,80}\1""",
    re.I,
)
INPUT_TAG = re.compile(r"<(input|textarea)\b[^>]*>", re.I | re.S)
STORE_APIS = (
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "openDatabase",
    "document.cookie",
    "postMessage",
)


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_pages_exist_and_mention_wallet(path: Path):
    assert path.is_file(), f"missing {path}"
    text = path.read_text(encoding="utf-8")
    assert "stellarConnect" in text
    assert "stellarLookup" in text
    low = text.lower()
    assert "never your main wallet" in low
    assert "yogibear1323.github.io/alpha-hive" in low
    assert "never stores your key" in low or "never stores your key" in text.lower()


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_only_allowed_secret_key_input(path: Path):
    text = path.read_text(encoding="utf-8")
    offenders = []
    found_allowed = False
    for tag in INPUT_TAG.finditer(text):
        raw = tag.group(0)
        if re.search(r'id=["\']bridgeTok["\']', raw):
            continue
        ident = re.search(r'id=["\']([^"\']+)["\']', raw)
        allowed = ident and ident.group(1) in ALLOWED_SECRET_IDS
        if allowed:
            found_allowed = True
            assert re.search(r'type=["\']password["\']', raw, re.I), raw
            assert re.search(r'autocomplete=["\']off["\']', raw, re.I), raw
            assert re.search(r'spellcheck=["\']false["\']', raw, re.I), raw
            assert re.search(r'autocapitalize=["\']off["\']', raw, re.I), raw
            ph = re.search(r"""placeholder\s*=\s*(['"])(.*?)\1""", raw, re.I | re.S)
            if ph:
                assert not re.match(r"S[A-Z0-9]", ph.group(2).strip()), raw
            continue
        if SECRET_PLACEHOLDER.search(raw) or SECRET_ATTR.search(raw):
            offenders.append(raw)
        ph = re.search(r"""placeholder\s*=\s*(['"])(.*?)\1""", raw, re.I | re.S)
        if ph and re.match(r"S[A-Z0-9]", ph.group(2).strip()):
            offenders.append(raw)
    assert offenders == [], f"{path} has extra secret-key-like inputs: {offenders}"
    assert found_allowed, f"{path} is missing the hiveAccountSecret field"


def test_wallet_js_has_no_secret_key_input():
    assert OUR_JS.is_file()
    text = OUR_JS.read_text(encoding="utf-8")
    assert "createElement(\"input\")" not in text or "secret" not in text.lower()
    for tag in INPUT_TAG.finditer(text):
        raw = tag.group(0)
        assert not SECRET_PLACEHOLDER.search(raw)
        assert not SECRET_ATTR.search(raw)
        ph = re.search(r"""placeholder\s*=\s*(['"])(.*?)\1""", raw, re.I | re.S)
        if ph:
            assert not re.match(r"S[A-Z0-9]", ph.group(2).strip()), raw
    assert "starts with G" in text or "G[A-Z2-7]" in text


def test_public_address_lookup_is_g_not_s():
    html = (ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="stellarLookup"', html)
    field = re.search(r'<input[^>]*id="stellarLookup"[^>]*>', html)
    assert field, "missing lookup input"
    raw = field.group(0)
    assert "password" not in raw.lower()
    ph = re.search(r"""placeholder\s*=\s*(['"])(.*?)\1""", raw)
    assert ph, raw
    assert not re.match(r"S[A-Z0-9]", ph.group(2).strip())
    assert "G" in ph.group(2)


def test_key_session_module_never_uses_storage_apis():
    assert KEY_SESSION.is_file()
    text = KEY_SESSION.read_text(encoding="utf-8")
    hits = [w for w in STORE_APIS if w in text]
    assert hits == [], f"key-session mentions store APIs: {hits}"
    # Must not persist via location or cross-page query
    assert not re.search(r"location\s*\.\s*(search|hash|href)\s*=", text)


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_secret_field_is_not_written_to_storage(path: Path):
    text = path.read_text(encoding="utf-8")
    # The in-memory field may be read into a local var named secret, then cleared.
    # It must never be handed to a browser store or a URL.
    patterns = [
        r"localStorage[\s\S]{0,120}hiveAccountSecret",
        r"sessionStorage[\s\S]{0,120}hiveAccountSecret",
        r"indexedDB[\s\S]{0,120}hiveAccountSecret",
        r"document\.cookie[\s\S]{0,80}hiveAccountSecret",
        r"hiveAccountSecret[\s\S]{0,120}localStorage",
        r"hiveAccountSecret[\s\S]{0,120}sessionStorage",
    ]
    hits = [pat for pat in patterns if re.search(pat, text)]
    assert hits == []
    assert re.search(r"secretEl\.value\s*=\s*[\"']{2}", text) or re.search(
        r"hiveAccountSecret[^\n]{0,80}\.value\s*=\s*[\"']{2}", text
    ), "sign-in must clear the key field after parsing"


def test_pages_have_csp_on_key_holding_document():
    text = (ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in text
    assert "unsafe-eval" not in text
    assert "horizon.stellar.org" in text
    assert "horizon-testnet.stellar.org" in text
    assert "friendbot.stellar.org" in text


def test_homepage_create_deep_links_before_any_key():
    for rel in ("index.html", "web/landing.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "Runs on the Stellar network." in text
        assert "terminal/?signup=1" in text
        assert "terminal/?signin=1" in text
        assert "Create Alpha Hive account" in text
        # The homepage must not collect a key; generation happens on the terminal page.
        assert 'id="hiveAccountSecret"' not in text
        assert "SAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in text


def test_terminal_signup_query_does_not_carry_a_key():
    text = (ROOT / "terminal" / "index.html").read_text(encoding="utf-8")
    assert 'params.has("signup")' in text
    assert 'params.has("signin")' in text
    assert "generateAccount" in text
    assert "Fund with Friendbot" in text
    assert "I've saved my secret key" in text or "I’ve saved my secret key" in text
