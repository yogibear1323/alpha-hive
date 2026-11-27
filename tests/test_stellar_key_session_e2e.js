/**
 * Testnet end-to-end: sign and submit with a throwaway Friendbot key
 * through the in-memory Alpha Hive account session.
 *
 * Never runs against mainnet. Never used by the page.
 * Secret keys stay in memory in this script and are never logged or written.
 *
 * Run: node --test tests/test_stellar_key_session_e2e.js
 * Requires @stellar/stellar-sdk (npm install in tools/stellar-kit).
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");

const ROOT = path.join(__dirname, "..");
const MOD = path.join(ROOT, "terminal", "vendor", "stellar-key-session.js");
const SDK_PATH = path.join(ROOT, "tools", "stellar-kit", "node_modules", "@stellar", "stellar-sdk");
const HORIZON_TESTNET = "https://horizon-testnet.stellar.org";
const FRIENDBOT = "https://friendbot.stellar.org";
const PUBLIC_HORIZON = "https://horizon.stellar.org";

function loadSdk() {
  if (!fs.existsSync(SDK_PATH)) return null;
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
      const body = await res.text().catch(() => "");
      if (/already/i.test(body) || /create_account/i.test(body) || /op_already_exists/i.test(body)) return;
      last += " " + body.slice(0, 200);
    }
    await sleep(1500 * (i + 1));
  }
  throw new Error("Friendbot funding failed: " + last);
}

describe("testnet key-session sign+submit e2e (throwaway keypair only)", () => {
  it("signs a self-payment with the in-memory key and submits on testnet only", async () => {
    const StellarSdk = loadSdk();
    if (!StellarSdk) {
      assert.ok(true, "skip: stellar-sdk is not installed in tools/stellar-kit");
      return;
    }
    const { Keypair, Horizon, TransactionBuilder, Transaction, Operation, Asset, Networks } = StellarSdk;
    assert.equal(HORIZON_TESTNET.includes("testnet"), true);
    assert.equal(PUBLIC_HORIZON.includes("testnet"), false);

    delete require.cache[require.resolve(MOD)];
    const ks = require(MOD);
    const keypair = Keypair.random();
    const publicKey = keypair.publicKey();
    // Do not print or write keypair.secret().
    const created = ks.createAccount({ address: publicKey, secret: keypair.secret(), network: "testnet" });
    assert.equal(created.ok, true);
    assert.equal(created.address, publicKey);
    assert.equal(ks.network(), "testnet");

    await fund(publicKey);
    const server = new Horizon.Server(HORIZON_TESTNET);
    const account = await server.loadAccount(publicKey);
    const fee = await server.fetchBaseFee();
    const tx = new TransactionBuilder(account, { fee: String(fee), networkPassphrase: Networks.TESTNET })
      .addOperation(Operation.payment({ destination: publicKey, asset: Asset.native(), amount: "1" }))
      .setTimeout(180)
      .build();
    const signedXdr = ks.signXDR(tx.toXDR(), Networks.TESTNET);
    assert.equal(typeof signedXdr, "string");
    assert.equal(/S[A-Z2-7]{55}/.test(signedXdr), false);
    const signedTx = new Transaction(signedXdr, Networks.TESTNET);
    const res = await server.submitTransaction(signedTx);
    const hash = res && (res.hash || res.id);
    assert.ok(hash && String(hash).length > 8, "expected a submission hash");
    ks.logout();
    assert.equal(ks.isSignedIn(), false);
  });
});
