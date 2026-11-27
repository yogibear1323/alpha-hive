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

describe("usdc issuers", () => {
  it("uses Circle mainnet USDC and labels the testnet issuer as test", () => {
    assert.equal(w.usdcIssuer("public"), w.USDC_ISSUER);
    assert.equal(w.usdcIssuer("testnet"), w.USDC_ISSUER_TESTNET);
    assert.equal(w.usdcIssuer("nope"), w.USDC_ISSUER);
    assert.equal(w.USDC_ISSUER, "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN");
    assert.equal(w.USDC_ISSUER_TESTNET, "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5");
    assert.notEqual(w.USDC_ISSUER, w.USDC_ISSUER_TESTNET);
  });

  it("treats only the official issuer for that network as official USDC", () => {
    const acc = {
      balances: [
        { asset_type: "native", balance: "10" },
        { asset_type: "credit_alphanum4", asset_code: "USDC", asset_issuer: w.USDC_ISSUER, balance: "3" },
        { asset_type: "credit_alphanum4", asset_code: "USDC", asset_issuer: w.USDC_ISSUER_TESTNET, balance: "9" },
      ],
    };
    const main = w.parseBalances(acc);
    assert.equal(main.officialUsdc, true);
    assert.equal(main.usdc, 3);
    const test = w.parseBalances(acc, { usdcIssuer: w.USDC_ISSUER_TESTNET });
    assert.equal(test.officialUsdc, true);
    assert.equal(test.usdc, 9);
  });
});

describe("stroops and min received", () => {
  it("converts to and from stroops without rounding up", () => {
    assert.equal(w.toStroops("12.7066798"), 127066798n);
    assert.equal(w.fromStroops(127066798n), "12.7066798");
    assert.equal(w.fromStroops(w.toStroops("99.5")), "99.5");
    assert.equal(w.normalizeAmount("1.2300000"), "1.23");
    assert.equal(w.toStroops("not-a-number"), null);
    assert.equal(w.toStroops(""), null);
  });

  it("floors destMin so 0.5% slippage never rounds up", () => {
    assert.equal(w.minReceived("100", 0.5), "99.5");
    assert.equal(w.minReceived("100", 0), "100");
    assert.equal(w.minReceived("1", 0.5), "0.995");
    // 12.7066798 * 0.995 floored to 7 decimals = 12.6431464
    assert.equal(w.minReceived("12.7066798", 0.5), "12.6431464");
    // 1 stroop at 0.5% floors to 0
    assert.equal(w.minReceived("0.0000001", 0.5), "0");
    assert.equal(w.minReceived("10", 100), null);
    assert.equal(w.minReceived("10", -1), null);
    assert.equal(w.minReceived("0", 0.5), null);
  });
});

describe("reserve and spendable XLM", () => {
  it("reserves 1 XLM for the account plus 0.5 XLM per entry", () => {
    const acc = {
      subentry_count: 1,
      num_sponsoring: 0,
      num_sponsored: 0,
      balances: [
        { asset_type: "native", balance: "10.0000000", selling_liabilities: "0.5000000" },
      ],
    };
    // 2 (account) + 1 subentry = 3 * 0.5 = 1.5 reserve; cushion 0.01
    assert.equal(w.reserveXlm(acc), 1.5);
    assert.equal(w.spendableXlm(acc), 10 - 0.5 - 1.5 - 0.01);
    assert.equal(w.spendableXlm(acc, { cushion: 0 }), 8);
    assert.equal(w.spendableXlm({ balances: [] }), 0);
  });
});

describe("USDC trustline", () => {
  it("detects an official USDC trustline even at a zero balance", () => {
    const acc = {
      balances: [
        { asset_type: "native", balance: "5" },
        { asset_type: "credit_alphanum4", asset_code: "USDC", asset_issuer: w.USDC_ISSUER, balance: "0.0000000" },
      ],
    };
    assert.equal(w.hasUsdcTrustline(acc, w.USDC_ISSUER), true);
    assert.equal(w.hasUsdcTrustline(acc, w.USDC_ISSUER_TESTNET), false);
    assert.equal(w.hasUsdcTrustline({ balances: [{ asset_type: "native", balance: "5" }] }, w.USDC_ISSUER), false);
  });
});

describe("strict-send quote", () => {
  it("builds a Horizon strict-send URL for XLM to official USDC", () => {
    const url = w.strictSendUrl("public", "100.5", w.USDC_ISSUER);
    assert.match(url, /^https:\/\/horizon\.stellar\.org\/paths\/strict-send\?/);
    assert.match(url, /source_asset_type=native/);
    assert.match(url, /source_amount=100\.5/);
    assert.match(url, /destination_assets=USDC%3AGA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN/);
    const testUrl = w.strictSendUrl("testnet", "10");
    assert.match(testUrl, /^https:\/\/horizon-testnet\.stellar\.org\/paths\/strict-send\?/);
    assert.match(testUrl, /GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5/);
  });

  it("picks the path with the most USDC out and applies min received", () => {
    const body = {
      _embedded: {
        records: [
          {
            source_amount: "100.0000000",
            destination_amount: "12.7019000",
            destination_asset_code: "USDC",
            destination_asset_issuer: w.USDC_ISSUER,
            path: [],
          },
          {
            source_amount: "100.0000000",
            destination_amount: "12.7066798",
            destination_asset_code: "USDC",
            destination_asset_issuer: w.USDC_ISSUER,
            path: [
              { asset_type: "credit_alphanum4", asset_code: "SHX", asset_issuer: "GDSTRSHXHGJ7ZIVRBXEYE5Q74XUVCUSEKEBR7UCHEUUEK72N7I7KJ6JH" },
            ],
          },
        ],
      },
    };
    const q = w.parseStrictSendQuote(body, 0.5);
    assert.equal(q.ok, true);
    assert.equal(q.destAmount, "12.7066798");
    assert.equal(q.destMin, "12.6431464");
    assert.equal(q.route, "XLM \u2192 SHX \u2192 USDC");
    assert.equal(q.path.length, 1);
    const empty = w.parseStrictSendQuote({ _embedded: { records: [] } }, 0.5);
    assert.equal(empty.ok, false);
    assert.equal(empty.error, "no_path");
  });
});

describe("validateConvert", () => {
  it("requires a positive amount, slippage cap, spendable headroom, trustline and destMin", () => {
    const goodQuote = { ok: true, destMin: "9.95" };
    const ok = w.validateConvert({
      sendAmount: "10",
      slippagePct: 0.5,
      spendableXlm: 20,
      requireTrustline: true,
      hasTrustline: true,
      quote: goodQuote,
    });
    assert.equal(ok.ok, true);
    const over = w.validateConvert({ sendAmount: "30", slippagePct: 0.5, spendableXlm: 20 });
    assert.ok(over.errors.includes("spendable"));
    const slip = w.validateConvert({ sendAmount: "1", slippagePct: 50 });
    assert.ok(slip.errors.includes("slippage"));
    const tl = w.validateConvert({
      sendAmount: "1",
      slippagePct: 0.5,
      requireTrustline: true,
      hasTrustline: false,
    });
    assert.ok(tl.errors.includes("trustline"));
    const tiny = w.validateConvert({
      sendAmount: "1",
      slippagePct: 0.5,
      quote: { ok: true, destMin: "0" },
    });
    assert.ok(tiny.errors.includes("dest_min"));
  });
});
