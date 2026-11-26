/**
 * Unit tests for the Alpha Hive Stellar wallet helpers.
 * Run: node --test tests/test_stellar_wallet.js
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const w = require(path.join(__dirname, "..", "terminal", "vendor", "stellar-wallet.js"));

const USDC = w.USDC_ISSUER;
const SDF = w.SDF_ACCOUNT;

describe("address helpers", () => {
  it("accepts a public G... address and rejects secrets or junk", () => {
    assert.equal(w.isValidAddress(SDF), true);
    assert.equal(w.isValidAddress(" " + SDF + " "), true);
    assert.equal(w.isValidAddress("S" + SDF.slice(1)), false);
    assert.equal(w.isValidAddress("g" + SDF.slice(1)), false);
    assert.equal(w.isValidAddress(SDF.slice(0, 55)), false);
    assert.equal(w.isValidAddress(""), false);
    assert.equal(w.isValidAddress(null), false);
  });

  it("shortens a public address with an ellipsis", () => {
    assert.equal(w.shortenAddress(SDF), "GB6NVE\u2026Y4AQ");
    assert.equal(w.shortenAddress("SHORT"), "SHORT");
    assert.equal(w.shortenAddress(""), "");
  });
});

describe("networks", () => {
  it("defaults to public Horizon and can switch to testnet", () => {
    assert.equal(w.horizonUrl("public"), "https://horizon.stellar.org");
    assert.equal(w.horizonUrl("testnet"), "https://horizon-testnet.stellar.org");
    assert.equal(w.horizonUrl("nope"), "https://horizon.stellar.org");
    assert.match(w.networkPassphrase("public"), /Public Global Stellar Network/);
    assert.match(w.networkPassphrase("testnet"), /Test SDF Network/);
    assert.equal(w.accountUrl("public", SDF), "https://horizon.stellar.org/accounts/" + SDF);
  });
});

describe("parseBalances", () => {
  it("reads XLM, official USDC, and other assets", () => {
    const parsed = w.parseBalances({
      balances: [
        { asset_type: "native", balance: "12.5" },
        { asset_type: "credit_alphanum4", asset_code: "USDC", asset_issuer: USDC, balance: "40.25" },
        { asset_type: "credit_alphanum4", asset_code: "BTC", asset_issuer: "GBTCISSUERAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHJ", balance: "0.01" },
        { asset_type: "liquidity_pool_shares", liquidity_pool_id: "abc", balance: "3" },
      ],
    });
    assert.equal(parsed.xlm, 12.5);
    assert.equal(parsed.usdc, 40.25);
    assert.equal(parsed.officialUsdc, true);
    assert.equal(parsed.others.length, 2);
    assert.ok(parsed.sendable.some((a) => a.native));
    assert.ok(parsed.sendable.some((a) => a.usdc));
    assert.ok(!parsed.sendable.some((a) => a.pool));
  });

  it("handles an account with only XLM", () => {
    const parsed = w.parseBalances({ balances: [{ asset_type: "native", balance: "1975727279.7861417" }] });
    assert.ok(parsed.xlm > 1e9);
    assert.equal(parsed.usdc, 0);
    assert.equal(parsed.others.length, 0);
  });
});

describe("usdValue", () => {
  it("prices XLM from the feed and treats official USDC as $1", () => {
    const v = w.usdValue({ xlm: 10, usdc: 5 }, 0.5);
    assert.equal(v.usd, 10);
    assert.equal(v.xlmUsd, 0.5);
    assert.equal(v.priced, true);
    const missing = w.usdValue({ xlm: 10, usdc: 0 }, null);
    assert.equal(missing.priced, false);
    assert.equal(missing.usd, 0);
  });
});

describe("memo and payment checks", () => {
  it("warns when the destination looks like an exchange", () => {
    assert.equal(w.isExchangeDomain("binance.com"), true);
    assert.equal(w.isExchangeDomain("www.Kraken.com"), true);
    assert.equal(w.isExchangeDomain("pro.coinbase.com"), true);
    assert.equal(w.isExchangeDomain("example.com"), false);
    const warn = w.memoAdvice({ memo: "", homeDomain: "binance.com" });
    assert.equal(warn.warnExchange, true);
    assert.equal(warn.missingMemo, true);
    assert.match(warn.message, /memo is required/i);
    const general = w.memoAdvice({ memo: "hi", homeDomain: "" });
    assert.equal(general.warnExchange, false);
    assert.match(general.message, /usually required/i);
  });

  it("classifies numeric memos as id and flags text over 28 bytes", () => {
    assert.deepEqual(w.chooseMemo(""), { type: "none", value: "" });
    assert.equal(w.chooseMemo("12345").type, "id");
    const text = w.chooseMemo("invoice-42");
    assert.equal(text.type, "text");
    assert.equal(text.tooLong, false);
    const long = w.chooseMemo("this memo is definitely longer than twenty-eight bytes");
    assert.equal(long.tooLong, true);
  });

  it("validates a payment payload", () => {
    const bad = w.validatePayment({ destination: "nope", amount: "-1", assetCode: "" });
    assert.equal(bad.ok, false);
    assert.deepEqual(bad.errors, ["destination", "amount", "asset"]);
    const ok = w.validatePayment({ destination: SDF, amount: "1.5", assetCode: "XLM", memo: "1" });
    assert.equal(ok.ok, true);
    assert.equal(ok.memo.type, "id");
  });
});

describe("sim starting balance", () => {
  it("requires at least $100", () => {
    assert.equal(w.canSetSimBalance(99.99), false);
    assert.equal(w.canSetSimBalance(100), true);
    assert.equal(w.canSetSimBalance(NaN), false);
  });

  it("fills an inBal field when present and idle", () => {
    const { JSDOM } = (() => { try { return { JSDOM: null }; } catch (e) { return { JSDOM: null }; } })();
    // Lightweight stand-in document
    const fields = { inBal: { value: "10000", disabled: false, dispatchEvent() {} } };
    const balNum = { textContent: "" };
    const doc = { getElementById(id) { return id === "inBal" ? fields.inBal : id === "balNum" ? balNum : null; } };
    const res = w.applySimStartingBalance(1234.567, doc);
    assert.equal(res.ok, true);
    assert.equal(res.value, 1234.57);
    assert.equal(fields.inBal.value, "1234.57");
    assert.equal(balNum.textContent, "$1,234.57");
    fields.inBal.disabled = true;
    assert.equal(w.applySimStartingBalance(500, doc).error, "session_running");
  });
});

describe("formatters", () => {
  it("formats amounts and USD", () => {
    assert.equal(w.formatUsd(10), "$10.00");
    assert.equal(w.formatUsd(-2.5), "-$2.50");
    assert.equal(w.formatAmount(12.5000000), "12.5");
    assert.equal(w.formatAmount(NaN), "\u2014");
  });
});
