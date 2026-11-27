/**
 * Regression checks on terminal/index.html account UI source.
 * Run: node --test tests/test_account_ui_source.js
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "terminal", "index.html"), "utf8");

describe("account UI source", () => {
  it("inverts aria-selected on the Sign in tab", () => {
    assert.match(src, /si\.setAttribute\("aria-selected",create\?"false":"true"\)/);
    assert.doesNotMatch(
      src,
      /if\(si\)\{[^}]*setAttribute\("aria-selected",create\?"true":"false"\)/
    );
  });

  it("keeps a Sign in control in the header chip bar", () => {
    assert.match(src, /id="keySignInBtn"/);
    const chips = src.slice(src.indexOf('class="chips"'), src.indexOf('class="chips"') + 1200);
    assert.match(chips, /id="keySignInBtn"/);
    assert.doesNotMatch(
      src.slice(src.indexOf('id="keySignInBtn"'), src.indexOf('id="keySignInBtn"') + 80),
      /\bid="keySignInBtn"[^>]*hidden/
    );
  });

  it("loads the vendored QR library without a CDN", () => {
    assert.match(src, /qrcode-generator\.js/);
    assert.match(src, /stellar-qr\.js/);
    assert.doesNotMatch(src, /cdnjs|jsdelivr|unpkg|googleapis/);
    assert.match(src, /id="fundFriendbot"/);
    assert.match(src, /id="fundPanel"/);
    assert.match(src, /paintFund/);
  });
});
