"""Scan terminal pages and our wallet JS for any Stellar secret-key input.

A Stellar secret key starts with S. Alpha Hive must never show a field that
asks the user to type one. The existing broker 'bridge token' password field
is unrelated and is allowed.
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

# Inputs that look like they collect a Stellar secret key (starts with S).
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


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_pages_exist_and_mention_wallet(path: Path):
    assert path.is_file(), f"missing {path}"
    text = path.read_text(encoding="utf-8")
    assert "stellarConnect" in text
    assert "stellarLookup" in text
    assert "never type a Stellar secret key" in text or "never type a Stellar secret key" in text.lower()


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_no_secret_key_inputs_in_pages(path: Path):
    text = path.read_text(encoding="utf-8")
    offenders = []
    for tag in INPUT_TAG.findall(text) if False else INPUT_TAG.finditer(text):
        raw = tag.group(0)
        # Skip the existing local-bridge token field (not a Stellar secret).
        if re.search(r'id=["\']bridgeTok["\']', raw):
            continue
        if SECRET_PLACEHOLDER.search(raw) or SECRET_ATTR.search(raw):
            offenders.append(raw)
        # An S... placeholder is always a secret-key tell, even without those words.
        ph = re.search(r"""placeholder\s*=\s*(['"])(.*?)\1""", raw, re.I | re.S)
        if ph and re.match(r"S[A-Z0-9]", ph.group(2).strip()):
            offenders.append(raw)
    assert offenders == [], f"{path} has secret-key-like inputs: {offenders}"


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
    # Must refuse secret-shaped strings as a "public" address.
    assert "starts with G" in text or "G[A-Z2-7]" in text


def test_public_address_lookup_is_g_not_s():
    html = (ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    # The lookup field is for public G... addresses only.
    assert re.search(r'id="stellarLookup"', html)
    field = re.search(r'<input[^>]*id="stellarLookup"[^>]*>', html)
    assert field, "missing lookup input"
    raw = field.group(0)
    assert "password" not in raw.lower()
    ph = re.search(r"""placeholder\s*=\s*(['"])(.*?)\1""", raw)
    assert ph, raw
    assert not re.match(r"S[A-Z0-9]", ph.group(2).strip())
    assert "G" in ph.group(2)
