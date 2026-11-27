/**
 * In-memory Alpha Hive account session: derive, match, wipe, no store APIs.
 * Run: node --test tests/test_stellar_key_session.js
 * Needs @stellar/stellar-sdk in tools/stellar-kit (same as the convert e2e).
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const MOD = path.join(ROOT, "terminal", "vendor", "stellar-key-session.js");
const SDK_PATH = path.join(ROOT, "tools", "stellar-kit", "node_modules", "@stellar", "stellar-sdk");

function loadSdk() {
  if (!fs.existsSync(SDK_PATH)) return null;
  return require(SDK_PATH);
}

describe("key-session source never touches browser stores", () => {
  const src = fs.readFileSync(MOD, "utf8");
  it("does not mention storage or transport APIs", () => {
    // This file must not persist or exfiltrate a key. The identifiers below
    // must not appear in the module at all (including comments).
    const banned = [
      "localStorage",
      "sessionStorage",
      "indexedDB",
      "openDatabase",
      "document.cookie",
      "postMessage",
      "local-storage",
      "session-storage",
    ];
    const hits = banned.filter((w) => src.includes(w));
    assert.deepEqual(hits, []);
  });

  it("does not write location.search or hash", () => {
    assert.equal(/location\s*\.\s*(search|hash|href)\s*=/.test(src), false);
  });
});

describe("address and key match", () => {
  it("creates only when the key derives the same public address", () => {
    const StellarSdk = loadSdk();
    if (!StellarSdk) {
      assert.ok(true, "skip: stellar-sdk is not installed in tools/stellar-kit");
      return;
    }
    // Fresh module state per require cache: delete and reload.
    delete require.cache[require.resolve(MOD)];
    const ks = require(MOD);
    const a = StellarSdk.Keypair.random();
    const b = StellarSdk.Keypair.random();
    // Do not print a.secret() or b.secret().
    const pubA = a.publicKey();
    const pubB = b.publicKey();
    assert.equal(ks.isValidAddress(pubA), true);
    assert.equal(ks.publicFromSecret(a.secret()), pubA);

    const mismatch = ks.createAccount({ address: pubA, secret: b.secret(), network: "testnet" });
    assert.equal(mismatch.ok, false);
    assert.equal(mismatch.error, "key_mismatch");
    assert.equal(ks.isSignedIn(), false);

    const bad = ks.createAccount({ address: pubA, secret: "not-a-key", network: "testnet" });
    assert.equal(bad.ok, false);

    const ok = ks.createAccount({ address: pubA, secret: a.secret(), network: "testnet" });
    assert.equal(ok.ok, true);
    assert.equal(ok.address, pubA);
    assert.equal(ok.network, "testnet");
    assert.equal(ks.isSignedIn(), true);
    assert.equal(ks.address(), pubA);
    assert.equal(ks.state().signedIn, true);
    // Public state must never include the seed or a secret-shaped value.
    const snap = ks.state();
    assert.equal("seed" in snap || "secret" in snap, false);
    const blob = JSON.stringify(snap);
    assert.equal(/S[A-Z2-7]{55}/.test(blob), false);
    ks.logout();
  });

  it("signs in from the key alone and derives the address", () => {
    const StellarSdk = loadSdk();
    if (!StellarSdk) {
      assert.ok(true, "skip: stellar-sdk is not installed in tools/stellar-kit");
      return;
    }
    delete require.cache[require.resolve(MOD)];
    const ks = require(MOD);
    const a = StellarSdk.Keypair.random();
    const pub = a.publicKey();
    const r = ks.signIn({ secret: a.secret() });
    assert.equal(r.ok, true);
    assert.equal(r.address, pub);
    assert.equal(r.network, "testnet");
    assert.equal(ks.shorten(pub), pub.slice(0, 6) + "\u2026" + pub.slice(-4));
    ks.logout();
  });
});

describe("generateAccount", () => {
  it("makes a fresh random keypair that matches, without signing in", () => {
    const StellarSdk = loadSdk();
    if (!StellarSdk) {
      assert.ok(true, "skip: stellar-sdk is not installed in tools/stellar-kit");
      return;
    }
    delete require.cache[require.resolve(MOD)];
    const ks = require(MOD);
    const a = ks.generateAccount({ network: "testnet" });
    const b = ks.generateAccount({ network: "testnet" });
    assert.equal(a.ok, true);
    assert.equal(b.ok, true);
    assert.equal(ks.isValidAddress(a.address), true);
    assert.equal(ks.isValidSecret(a.secret), true);
    assert.equal(ks.publicFromSecret(a.secret), a.address);
    assert.equal(a.network, "testnet");
    assert.notEqual(a.address, b.address);
    assert.notEqual(a.secret, b.secret);
    // Generation must not hold the key until createAccount / signIn.
    assert.equal(ks.isSignedIn(), false);
    assert.equal(ks.address(), "");
    const snap = ks.state();
    assert.equal("seed" in snap || "secret" in snap, false);
    const blob = JSON.stringify(snap);
    assert.equal(/S[A-Z2-7]{55}/.test(blob), false);
    if (!ks.formatBackup(a.address, a.secret, "testnet").includes(a.address)) {
      assert.fail("backup text missing the public address");
    }
    if (!ks.formatBackup(a.address, a.secret, "testnet").includes(a.secret)) {
      assert.fail("backup text missing the secret key");
    }
    const held = ks.createAccount({ address: a.address, secret: a.secret, network: "testnet" });
    assert.equal(held.ok, true);
    assert.equal(held.address, a.address);
    assert.equal(ks.isSignedIn(), true);
    const after = JSON.stringify(ks.state());
    assert.equal(/S[A-Z2-7]{55}/.test(after), false);
    ks.logout();
  });
});

describe("wipe on logout and idle", () => {
  it("logout drops the session so signing fails", () => {
    const StellarSdk = loadSdk();
    if (!StellarSdk) {
      assert.ok(true, "skip: stellar-sdk is not installed in tools/stellar-kit");
      return;
    }
    delete require.cache[require.resolve(MOD)];
    const ks = require(MOD);
    const a = StellarSdk.Keypair.random();
    assert.equal(ks.signIn({ secret: a.secret() }).ok, true);
    ks.logout();
    assert.equal(ks.isSignedIn(), false);
    assert.equal(ks.address(), "");
    assert.throws(() => ks.signXDR("AAAA", "Test SDF Network ; September 2015"));
  });

  it("wipes after 15 minutes of idle", () => {
    const StellarSdk = loadSdk();
    if (!StellarSdk) {
      assert.ok(true, "skip: stellar-sdk is not installed in tools/stellar-kit");
      return;
    }
    delete require.cache[require.resolve(MOD)];
    const ks = require(MOD);
    let pending = null;
    ks._setSchedule({
      now: () => 0,
      delay: (fn, ms) => {
        assert.equal(ms, ks.IDLE_MS);
        pending = fn;
        return 1;
      },
      cancel: () => { pending = null; },
    });
    const a = StellarSdk.Keypair.random();
    assert.equal(ks.signIn({ secret: a.secret() }).ok, true);
    assert.equal(typeof pending, "function");
    pending();
    assert.equal(ks.isSignedIn(), false);
    assert.equal(ks.address(), "");
  });
});
