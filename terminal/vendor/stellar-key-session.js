/**
 * In-memory Alpha Hive account (Stellar key) session.
 *
 * The key lives in a closure in this process only. This module does not write
 * it to the page, the network, or any browser store, and it does not put it
 * in a URL or a cross-origin message. Wipe on logout, page hide, and idle.
 * generateAccount uses Keypair.random() (crypto.getRandomValues) and returns
 * the secret once to the caller so it can be shown and saved; it does not
 * sign the session in until createAccount or signIn holds the key.
 *
 * Node tests can load this file directly. The browser attaches it as
 * AlphaHiveKeySession after the Stellar kit script.
 */
(function (root, factory) {
  var api = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AlphaHiveKeySession = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  var IDLE_MS = 15 * 60 * 1000;
  var SECRET_RE = /^S[A-Z2-7]{55}$/;
  var ADDR_RE = /^G[A-Z2-7]{55}$/;
  var held = null;
  var timer = null;
  var listeners = [];
  var schedule = {
    now: function () { return Date.now(); },
    delay: function (fn, ms) { return setTimeout(fn, ms); },
    cancel: function (id) { clearTimeout(id); }
  };

  function notify(why) {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](why, publicState()); } catch (e) {}
    }
  }

  function loadSdk() {
    if (root && root.AlphaHiveStellarKit && root.AlphaHiveStellarKit.Keypair) {
      return root.AlphaHiveStellarKit;
    }
    try {
      var fs = require("fs");
      var path = require("path");
      var sdkPath = path.join(__dirname, "..", "..", "tools", "stellar-kit", "node_modules", "@stellar", "stellar-sdk");
      if (fs.existsSync(sdkPath)) return require(sdkPath);
    } catch (e) {}
    return null;
  }

  function libs() {
    var sdk = loadSdk();
    if (!sdk || !sdk.Keypair) throw new Error("Stellar toolkit is not available");
    return sdk;
  }

  function normalizeNetwork(n) {
    return n === "public" || n === "mainnet" || n === "live" ? "public" : "testnet";
  }

  function isValidAddress(addr) {
    return typeof addr === "string" && ADDR_RE.test(addr.trim());
  }

  function isValidSecret(raw) {
    if (typeof raw !== "string") return false;
    var s = raw.trim();
    if (!SECRET_RE.test(s)) return false;
    try {
      libs().Keypair.fromSecret(s);
      return true;
    } catch (e) {
      return false;
    }
  }

  function publicFromSecret(raw) {
    if (!isValidSecret(raw)) return null;
    try {
      return libs().Keypair.fromSecret(raw.trim()).publicKey();
    } catch (e) {
      return null;
    }
  }

  function publicState() {
    if (!held) return { signedIn: false, address: "", network: "testnet" };
    return { signedIn: true, address: held.address, network: held.network };
  }

  function shorten(addr) {
    if (!addr || addr.length < 10) return addr || "";
    return addr.slice(0, 6) + "\u2026" + addr.slice(-4);
  }

  function armIdle() {
    if (timer != null) schedule.cancel(timer);
    timer = null;
    if (!held) return;
    timer = schedule.delay(function () { wipe("idle"); }, IDLE_MS);
  }

  function bumpIdle() {
    if (!held) return;
    held.last = schedule.now();
    armIdle();
  }

  function hold(address, secret, network) {
    wipe("replace");
    held = {
      address: address,
      seed: secret.trim(),
      network: normalizeNetwork(network || "testnet"),
      last: schedule.now()
    };
    armIdle();
    notify("signin");
    return publicState();
  }

  function wipe(why) {
    if (held) {
      held.seed = "";
      held.address = "";
      held = null;
    }
    if (timer != null) {
      schedule.cancel(timer);
      timer = null;
    }
    notify(why || "wipe");
    return publicState();
  }

  // Keypair.random() uses crypto.getRandomValues in the browser
  // (crypto.randomBytes in Node). This module never holds the
  // returned secret until createAccount or signIn is called.
  function generateAccount(opts) {
    opts = opts || {};
    var L;
    try { L = libs(); } catch (e) {
      return { ok: false, error: "no_toolkit" };
    }
    if (!L.Keypair || typeof L.Keypair.random !== "function") {
      return { ok: false, error: "no_random" };
    }
    var kp = L.Keypair.random();
    var address = kp.publicKey();
    var secret = kp.secret();
    if (!isValidAddress(address) || !isValidSecret(secret)) {
      return { ok: false, error: "bad_key" };
    }
    if (publicFromSecret(secret) !== address) {
      return { ok: false, error: "key_mismatch" };
    }
    return {
      ok: true,
      address: address,
      secret: secret,
      network: normalizeNetwork(opts.network || "testnet")
    };
  }

  function formatBackup(address, secret, network) {
    var net = normalizeNetwork(network || "testnet");
    return [
      "Alpha Hive Stellar account",
      "Network: " + net,
      "Public address: " + (address || ""),
      "Secret key: " + (secret || ""),
      "",
      "Keep this file private. Alpha Hive does not store this key.",
      "If you lose the secret key, it cannot be recovered.",
      "Use a separate trading account, never your main wallet."
    ].join("\n");
  }

  function createAccount(opts) {
    opts = opts || {};
    var address = (opts.address || "").trim();
    var secret = (opts.secret || "").trim();
    if (!isValidAddress(address)) return { ok: false, error: "bad_address" };
    if (!isValidSecret(secret)) return { ok: false, error: "bad_key" };
    var derived = publicFromSecret(secret);
    if (!derived) return { ok: false, error: "bad_key" };
    if (derived !== address) return { ok: false, error: "key_mismatch" };
    return { ok: true, ...hold(derived, secret, opts.network) };
  }

  function signIn(opts) {
    opts = opts || {};
    var secret = (opts.secret || "").trim();
    if (!isValidSecret(secret)) return { ok: false, error: "bad_key" };
    var derived = publicFromSecret(secret);
    if (!derived) return { ok: false, error: "bad_key" };
    return { ok: true, ...hold(derived, secret, opts.network) };
  }

  function setNetwork(network) {
    if (!held) return { ok: false, error: "not_signed_in" };
    var next = normalizeNetwork(network);
    var changed = held.network !== next;
    held.network = next;
    bumpIdle();
    if (changed) notify("network");
    return { ok: true, network: held.network, public: held.network === "public" };
  }

  function signXDR(xdr, networkPassphrase) {
    if (!held || !held.seed) throw new Error("not_signed_in");
    if (!xdr) throw new Error("missing_xdr");
    bumpIdle();
    var L = libs();
    var tx = new L.Transaction(xdr, networkPassphrase);
    var kp = L.Keypair.fromSecret(held.seed);
    tx.sign(kp);
    return tx.toXDR();
  }

  function onChange(fn) {
    if (typeof fn === "function") listeners.push(fn);
    return function () {
      listeners = listeners.filter(function (x) { return x !== fn; });
    };
  }

  function attachPageWipe() {
    if (typeof document === "undefined") return;
    if (root.__ahKeyWipeBound) return;
    root.__ahKeyWipeBound = true;
    var go = function () { wipe("unload"); };
    root.addEventListener("pagehide", go);
    root.addEventListener("beforeunload", go);
    if (typeof document.addEventListener === "function") {
      ["keydown", "pointerdown", "click"].forEach(function (ev) {
        document.addEventListener(ev, function () { bumpIdle(); }, { passive: true });
      });
    }
  }

  attachPageWipe();

  return {
    IDLE_MS: IDLE_MS,
    isValidAddress: isValidAddress,
    isValidSecret: isValidSecret,
    publicFromSecret: publicFromSecret,
    generateAccount: generateAccount,
    formatBackup: formatBackup,
    createAccount: createAccount,
    signIn: signIn,
    wipe: wipe,
    logout: function () { return wipe("logout"); },
    bumpIdle: bumpIdle,
    setNetwork: setNetwork,
    signXDR: signXDR,
    state: publicState,
    isSignedIn: function () { return !!(held && held.seed); },
    address: function () { return held ? held.address : ""; },
    network: function () { return held ? held.network : "testnet"; },
    shorten: shorten,
    onChange: onChange,
    attachPageWipe: attachPageWipe,
    _setSchedule: function (s) { if (s) schedule = s; }
  };
});
