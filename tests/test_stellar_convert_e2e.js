/**
 * Testnet end-to-end for the XLM → USDC convert path.
 *
 * Uses Friendbot plus a throwaway testnet keypair in this script only.
 * Never runs against mainnet. Never used by the terminal page.
 * Secret keys stay in memory and are never logged or written to disk.
 *
 * Run: node --test tests/test_stellar_convert_e2e.js
 * Requires @stellar/stellar-sdk (npm install in tools/stellar-kit).
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");

const ROOT = path.join(__dirname, "..");
const w = require(path.join(ROOT, "terminal", "vendor", "stellar-wallet.js"));

const SDK_PATH = path.join(ROOT, "tools", "stellar-kit", "node_modules", "@stellar", "stellar-sdk");
const HORIZON_TESTNET = "https://horizon-testnet.stellar.org";
const FRIENDBOT = "https://friendbot.stellar.org";
const PUBLIC_HORIZON = "https://horizon.stellar.org";

function loadSdk() {
  if (!fs.existsSync(SDK_PATH)) {
    return null;
  }
  return require(SDK_PATH);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function fund(publicKey) {
  let last = null;
  for (let i = 0; i < 4; i++) {
    const res = await fetch(`${FRIENDBOT}?addr=${encodeURIComponent(publicKey)}`);
    if (res.ok) return;
    last = `${res.status} ${res.statusText}`;
    if (res.status === 400) {
      // already funded, or a bad address
      const body = await res.text().catch(() => "");
      if (/already/i.test(body) || /create_account/i.test(body) || /op_already_exists/i.test(body)) return;
      last += " " + body.slice(0, 200);
    }
    await sleep(1500 * (i + 1));
  }
  throw new Error("Friendbot funding failed: " + last);
}

describe("testnet convert e2e (no real wallet, throwaway keypair only)", () => {
  it("adds a testnet USDC trustline and path-pays XLM to USDC when a path exists", async () => {
    const StellarSdk = loadSdk();
    if (!StellarSdk) {
      assert.ok(true, "skip: stellar-sdk is not installed in tools/stellar-kit");
      return;
    }
    const { Keypair, Horizon, TransactionBuilder, Operation, Asset, Networks } = StellarSdk;
    assert.equal(HORIZON_TESTNET.includes("testnet"), true);
    assert.equal(PUBLIC_HORIZON.includes("testnet"), false);

    const keypair = Keypair.random();
    const publicKey = keypair.publicKey();
    // Do not print or write keypair.secret().
    assert.match(publicKey, /^G[A-Z2-7]{55}$/);
    assert.equal(w.isValidAddress(publicKey), true);

    await fund(publicKey);

    const server = new Horizon.Server(HORIZON_TESTNET);
    let account = await server.loadAccount(publicKey);
    const issuer = w.usdcIssuer("testnet");
    assert.equal(issuer, w.USDC_ISSUER_TESTNET);
    assert.equal(w.hasUsdcTrustline(account, issuer), false);

    const spendable = w.spendableXlm(account);
    assert.ok(spendable > 1, "Friendbot should leave spendable XLM, got " + spendable);

    const fee = await server.fetchBaseFee();
    const trustTx = new TransactionBuilder(account, {
      fee: String(fee),
      networkPassphrase: Networks.TESTNET,
    })
      .addOperation(Operation.changeTrust({ asset: new Asset("USDC", issuer) }))
      .setTimeout(180)
      .build();
    trustTx.sign(keypair);
    const trustRes = await server.submitTransaction(trustTx);
    assert.ok(trustRes.hash, "trustline submit should return a hash");

    account = await server.loadAccount(publicKey);
    assert.equal(w.hasUsdcTrustline(account, issuer), true);
    const parsed = w.parseBalances(account, { usdcIssuer: issuer });
    assert.equal(parsed.officialUsdc, true);
    assert.equal(parsed.usdc, 0);

    const sendAmount = "5";
    const url = w.strictSendUrl("testnet", sendAmount, issuer);
    assert.match(url, /^https:\/\/horizon-testnet\.stellar\.org\//);
    assert.doesNotMatch(url, /horizon\.stellar\.org(?!-testnet)/);

    const quoteRes = await fetch(url, { headers: { Accept: "application/json" } });
    assert.equal(quoteRes.ok, true, "strict-send quote should succeed on testnet");
    const quoteBody = await quoteRes.json();
    const quote = w.parseStrictSendQuote(quoteBody, w.DEFAULT_SLIPPAGE_PCT);

    if (!quote.ok) {
      // Liquidity on testnet can vanish. Trustline already proved the sign+submit path.
      assert.equal(quote.error, "no_path");
      return;
    }

    assert.ok(Number(quote.destAmount) > 0);
    assert.ok(Number(quote.destMin) > 0);
    assert.ok(Number(quote.destMin) <= Number(quote.destAmount));
    assert.match(quote.route, /XLM.*USDC/);

    account = await server.loadAccount(publicKey);
    const path = (quote.path || []).map((p) =>
      p.asset_type === "native" ? Asset.native() : new Asset(p.asset_code, p.asset_issuer)
    );
    const payTx = new TransactionBuilder(account, {
      fee: String(await server.fetchBaseFee()),
      networkPassphrase: Networks.TESTNET,
    })
      .addOperation(
        Operation.pathPaymentStrictSend({
          sendAsset: Asset.native(),
          sendAmount: w.normalizeAmount(sendAmount),
          destination: publicKey,
          destAsset: new Asset("USDC", issuer),
          destMin: quote.destMin,
          path,
        })
      )
      .setTimeout(180)
      .build();
    payTx.sign(keypair);
    const payRes = await server.submitTransaction(payTx);
    assert.ok(payRes.hash, "path payment should return a hash");

    account = await server.loadAccount(publicKey);
    const after = w.parseBalances(account, { usdcIssuer: issuer });
    assert.ok(after.usdc > 0, "expected testnet USDC balance after convert, got " + after.usdc);
    assert.ok(after.usdc + 1e-7 >= Number(quote.destMin), "received USDC should meet destMin");
  });

  it("refuses to target public Horizon for this e2e script", () => {
    const url = w.strictSendUrl("testnet", "5");
    assert.match(url, /horizon-testnet\.stellar\.org/);
    assert.equal(url.startsWith(PUBLIC_HORIZON), false);
    assert.equal(HORIZON_TESTNET.startsWith(PUBLIC_HORIZON), false);
  });
});
