/**
 * Alpha Hive Stellar wallet panel (option A: connect + read-only balances).
 * Pure helpers are exported as AlphaHiveStellarWallet (and module.exports in Node)
 * so tests can run without a browser. The UI mounts only when `document` exists.
 * Alpha Hive never asks for, stores, or transmits a Stellar secret key.
 */
(function (root, factory) {
  var api = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AlphaHiveStellarWallet = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  var USDC_ISSUER = "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN";
  /* Circle testnet USDC (no cash value). Always label it as test. */
  var USDC_ISSUER_TESTNET = "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5";
  var SDF_ACCOUNT = "GB6NVEN5HSUBKMYCE5ZOWSK5K23TBWRUQLZY3KNMXUZ3AQ2ESC4MY4AQ";
  var BASE_RESERVE_XLM = 0.5;
  var DEFAULT_SLIPPAGE_PCT = 0.5;
  var MAX_SLIPPAGE_PCT = 5;
  var FEE_CUSHION_XLM = 0.01;
  var STROOPS = 10000000;
  var NETWORKS = {
    public: {
      id: "public",
      label: "Public",
      horizon: "https://horizon.stellar.org",
      passphrase: "Public Global Stellar Network ; September 2015"
    },
    testnet: {
      id: "testnet",
      label: "Testnet",
      horizon: "https://horizon-testnet.stellar.org",
      passphrase: "Test SDF Network ; September 2015"
    }
  };
  var EXCHANGE_DOMAINS = [
    "binance.com", "coinbase.com", "kraken.com", "okx.com", "bitfinex.com",
    "crypto.com", "kucoin.com", "bybit.com", "gate.io", "mexc.com",
    "bitstamp.net", "gemini.com", "bitmex.com", "cex.io", "whitebit.com",
    "poloniex.com", "lbank.com", "bitget.com", "htx.com", "huobi.com",
    "uphold.com", "robinhood.com", "revolut.com", "nexo.com", "crypto.com"
  ];
  var REFRESH_MS = 30000;
  var MIN_SIM_USD = 100;

  function horizonUrl(network) {
    return (NETWORKS[network] || NETWORKS.public).horizon;
  }
  function networkPassphrase(network) {
    return (NETWORKS[network] || NETWORKS.public).passphrase;
  }
  function accountUrl(network, address) {
    return horizonUrl(network) + "/accounts/" + encodeURIComponent(address);
  }
  function usdcIssuer(network) {
    return network === "testnet" ? USDC_ISSUER_TESTNET : USDC_ISSUER;
  }
  function isValidAddress(addr) {
    if (typeof addr !== "string") return false;
    var a = addr.trim();
    if (!/^G[A-Z2-7]{55}$/.test(a)) return false;
    try {
      var kit = root.AlphaHiveStellarKit;
      if (kit && kit.StrKey && typeof kit.StrKey.isValidEd25519PublicKey === "function") {
        return kit.StrKey.isValidEd25519PublicKey(a);
      }
    } catch (e) {}
    return true;
  }
  function shortenAddress(addr, head, tail) {
    if (!addr) return "";
    head = head == null ? 6 : head;
    tail = tail == null ? 4 : tail;
    if (addr.length <= head + tail + 1) return addr;
    return addr.slice(0, head) + "\u2026" + addr.slice(-tail);
  }
  function parseBalances(account, opts) {
    opts = opts || {};
    var officialIssuer = opts.usdcIssuer || USDC_ISSUER;
    var rows = (account && account.balances) || [];
    var xlm = 0, usdc = 0, officialUsdc = false;
    var others = [], sendable = [], all = [];
    for (var i = 0; i < rows.length; i++) {
      var b = rows[i];
      var amount = parseFloat(b.balance);
      if (!isFinite(amount)) continue;
      if (b.asset_type === "native") {
        xlm = amount;
        var native = { code: "XLM", issuer: null, balance: amount, native: true };
        all.push(native);
        sendable.push(native);
        continue;
      }
      if (b.asset_type === "liquidity_pool_shares") {
        others.push({
          code: "LP",
          issuer: b.liquidity_pool_id || "",
          balance: amount,
          pool: true
        });
        continue;
      }
      var code = b.asset_code || "UNKNOWN";
      var issuer = b.asset_issuer || "";
      var rec = { code: code, issuer: issuer, balance: amount, native: false };
      if (code === "USDC" && issuer === officialIssuer) {
        usdc = amount;
        officialUsdc = true;
        rec.usdc = true;
        sendable.unshift(rec);
      } else {
        if (code === "USDC") rec.usdc = true;
        if (!officialUsdc && code === "USDC") usdc = amount;
        others.push(rec);
        sendable.push(rec);
      }
      all.push(rec);
    }
    if (officialUsdc) {
      /* keep official USDC as usdc; other USDC tokens stay in others */
    }
    return { xlm: xlm, usdc: usdc, officialUsdc: officialUsdc, others: others, sendable: sendable, all: all };
  }
  function usdValue(balances, xlmUsd) {
    var xlm = balances && balances.xlm;
    var usdc = balances && balances.usdc;
    var pricedXlm = typeof xlmUsd === "number" && isFinite(xlmUsd) && isFinite(xlm);
    var xlmPart = pricedXlm ? xlm * xlmUsd : 0;
    var usdcPart = isFinite(usdc) ? usdc : 0;
    return {
      usd: xlmPart + usdcPart,
      xlmUsd: pricedXlm ? xlmUsd : null,
      priced: pricedXlm || (isFinite(usdc) && usdc > 0)
    };
  }
  function formatAmount(n, maxFrac) {
    n = +n;
    if (!isFinite(n)) return "\u2014";
    maxFrac = maxFrac == null ? 7 : maxFrac;
    if (Math.abs(n) >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: Math.min(maxFrac, 2) });
    var s = n.toFixed(maxFrac).replace(/\.?0+$/, "");
    return s === "-0" ? "0" : s;
  }
  function formatUsd(n) {
    if (!isFinite(n)) return "\u2014";
    var sign = n < 0 ? "-" : "";
    return sign + "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function isExchangeDomain(domain) {
    if (!domain) return false;
    var d = String(domain).trim().toLowerCase().replace(/^www\./, "");
    if (!d) return false;
    for (var i = 0; i < EXCHANGE_DOMAINS.length; i++) {
      var x = EXCHANGE_DOMAINS[i];
      if (d === x || d.slice(-(x.length + 1)) === "." + x) return true;
    }
    return false;
  }
  function memoAdvice(opts) {
    opts = opts || {};
    var memoText = String(opts.memo || "").trim();
    var toExchange = !!opts.knownExchange || isExchangeDomain(opts.homeDomain);
    return {
      requiredPrompt: true,
      warnExchange: toExchange,
      missingMemo: !memoText,
      message: toExchange
        ? "This destination looks like an exchange or hosted wallet. A memo is required. Without it, your funds may be lost."
        : "If you are sending to an exchange or hosted wallet, a memo is usually required. Funds can be lost without it."
    };
  }
  function chooseMemo(value) {
    var text = String(value || "").trim();
    if (!text) return { type: "none", value: "" };
    if (/^\d+$/.test(text)) {
      try {
        if (BigInt(text) >= 0n && BigInt(text) <= 18446744073709551615n) {
          return { type: "id", value: text };
        }
      } catch (e) {}
    }
    var bytes = typeof TextEncoder !== "undefined" ? new TextEncoder().encode(text).length : text.length;
    return { type: "text", value: text, tooLong: bytes > 28, byteLength: bytes };
  }
  function validatePayment(fields) {
    fields = fields || {};
    var errors = [];
    if (!isValidAddress(fields.destination || "")) errors.push("destination");
    var amt = parseFloat(fields.amount);
    if (!isFinite(amt) || amt <= 0) errors.push("amount");
    if (!fields.assetCode) errors.push("asset");
    var memo = chooseMemo(fields.memo);
    if (memo.tooLong) errors.push("memo_too_long");
    return { ok: errors.length === 0, errors: errors, memo: memo };
  }
  function canSetSimBalance(usd) {
    return isFinite(usd) && usd >= MIN_SIM_USD;
  }
  function applySimStartingBalance(usd, doc) {
    doc = doc || (typeof document !== "undefined" ? document : null);
    if (!canSetSimBalance(usd)) return { ok: false, error: "too_small" };
    if (!doc) return { ok: false, error: "no_document" };
    var el = doc.getElementById("inBal");
    if (!el) return { ok: false, error: "missing_field" };
    if (el.disabled) return { ok: false, error: "session_running" };
    var rounded = Math.round(usd * 100) / 100;
    el.value = String(rounded);
    try { el.dispatchEvent(new Event("input", { bubbles: true })); } catch (e) {}
    var balNum = doc.getElementById("balNum");
    if (balNum) balNum.textContent = formatUsd(rounded);
    return { ok: true, value: rounded };
  }

  function toStroops(amount) {
    if (amount == null || amount === "") return null;
    var s = String(amount).trim();
    if (!/^-?\d+(\.\d+)?$/.test(s)) return null;
    var neg = s.charAt(0) === "-";
    if (neg) s = s.slice(1);
    var parts = s.split(".");
    var whole = parts[0] || "0";
    var frac = (parts[1] || "").slice(0, 7);
    while (frac.length < 7) frac += "0";
    try {
      var n = BigInt(whole) * BigInt(STROOPS) + BigInt(frac);
      return neg ? -n : n;
    } catch (e) {
      return null;
    }
  }
  function fromStroops(n) {
    if (n == null) return "";
    if (typeof n !== "bigint") {
      try { n = BigInt(n); } catch (e) { return ""; }
    }
    var neg = n < 0n;
    if (neg) n = -n;
    var whole = n / BigInt(STROOPS);
    var frac = (n % BigInt(STROOPS)).toString().padStart(7, "0").replace(/0+$/, "");
    var s = whole.toString() + (frac ? "." + frac : "");
    return neg ? "-" + s : s;
  }
  function normalizeAmount(amount) {
    var n = toStroops(amount);
    return n == null ? "" : fromStroops(n);
  }
  /* destMin = destAmount * (1 - slippagePct/100), floored to 7 decimals (never rounded up). */
  function minReceived(destAmount, slippagePct) {
    var dest = toStroops(destAmount);
    var slip = toStroops(slippagePct);
    if (dest == null || dest <= 0n || slip == null || slip < 0n) return null;
    if (slip >= 100n * BigInt(STROOPS)) return null; /* 100% or more */
    var slipFrac = slip / 100n; /* 0.5% -> 50_000 of 10_000_000 */
    var keep = BigInt(STROOPS) - slipFrac;
    if (keep < 0n) return null;
    return fromStroops((dest * keep) / BigInt(STROOPS));
  }
  function reserveXlm(account, baseReserve) {
    baseReserve = baseReserve == null ? BASE_RESERVE_XLM : Number(baseReserve);
    if (!isFinite(baseReserve) || baseReserve < 0) baseReserve = BASE_RESERVE_XLM;
    var sub = Number((account && account.subentry_count) || 0);
    var sponsoring = Number((account && account.num_sponsoring) || 0);
    var sponsored = Number((account && account.num_sponsored) || 0);
    var entries = 2 + sub + sponsoring - sponsored;
    if (entries < 0) entries = 0;
    return entries * baseReserve;
  }
  function spendableXlm(account, opts) {
    opts = opts || {};
    var baseReserve = opts.baseReserve == null ? BASE_RESERVE_XLM : opts.baseReserve;
    var cushion = opts.cushion == null ? FEE_CUSHION_XLM : opts.cushion;
    var rows = (account && account.balances) || [];
    var native = null;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].asset_type === "native") { native = rows[i]; break; }
    }
    if (!native) return 0;
    var bal = parseFloat(native.balance);
    var sell = parseFloat(native.selling_liabilities || "0");
    if (!isFinite(bal)) return 0;
    if (!isFinite(sell)) sell = 0;
    var s = bal - sell - reserveXlm(account, baseReserve) - (isFinite(cushion) ? cushion : 0);
    return s > 0 ? s : 0;
  }
  function hasUsdcTrustline(accountOrBalances, issuer) {
    issuer = issuer || USDC_ISSUER;
    var rows;
    if (!accountOrBalances) return false;
    if (Array.isArray(accountOrBalances)) rows = accountOrBalances;
    else if (accountOrBalances.balances) rows = accountOrBalances.balances;
    else if (accountOrBalances.all) rows = accountOrBalances.all;
    else return false;
    for (var i = 0; i < rows.length; i++) {
      var b = rows[i];
      var code = b.asset_code || b.code;
      var iss = b.asset_issuer || b.issuer;
      if (code === "USDC" && iss === issuer) return true;
    }
    return false;
  }
  function formatRoute(record) {
    var parts = ["XLM"];
    var path = (record && record.path) || [];
    for (var i = 0; i < path.length; i++) {
      var p = path[i];
      parts.push(p.asset_type === "native" ? "XLM" : (p.asset_code || "?"));
    }
    parts.push((record && record.destination_asset_code) || "USDC");
    return parts.join(" \u2192 ");
  }
  function strictSendUrl(network, sendAmount, destIssuer) {
    destIssuer = destIssuer || usdcIssuer(network);
    var amount = normalizeAmount(sendAmount);
    return horizonUrl(network) + "/paths/strict-send?source_asset_type=native&source_amount=" +
      encodeURIComponent(amount) + "&destination_assets=" + encodeURIComponent("USDC:" + destIssuer);
  }
  function parseStrictSendQuote(body, slippagePct) {
    slippagePct = slippagePct == null ? DEFAULT_SLIPPAGE_PCT : slippagePct;
    var records = (body && body._embedded && body._embedded.records) || (Array.isArray(body) ? body : []);
    var best = null, bestDest = null;
    for (var i = 0; i < records.length; i++) {
      var r = records[i];
      var dest = toStroops(r && r.destination_amount);
      if (dest == null) continue;
      if (bestDest == null || dest > bestDest) { best = r; bestDest = dest; }
    }
    if (!best) return { ok: false, error: "no_path", records: records.length };
    var min = minReceived(best.destination_amount, slippagePct);
    return {
      ok: true,
      sendAmount: best.source_amount,
      destAmount: best.destination_amount,
      destMin: min,
      slippagePct: Number(slippagePct),
      path: best.path || [],
      route: formatRoute(best),
      destCode: best.destination_asset_code || "USDC",
      destIssuer: best.destination_asset_issuer,
      record: best
    };
  }
  function validateConvert(fields) {
    fields = fields || {};
    var errors = [];
    var amt = toStroops(fields.sendAmount);
    if (amt == null || amt <= 0n) errors.push("amount");
    var slip = toStroops(fields.slippagePct);
    if (slip == null || slip < 0n || slip > toStroops(MAX_SLIPPAGE_PCT)) errors.push("slippage");
    if (fields.spendableXlm != null && amt != null) {
      var cap = toStroops(fields.spendableXlm);
      if (cap == null || amt > cap) errors.push("spendable");
    }
    if (fields.requireTrustline && !fields.hasTrustline) errors.push("trustline");
    if (fields.quote && !fields.quote.ok) errors.push("quote");
    if (fields.quote && fields.quote.ok) {
      var min = toStroops(fields.quote.destMin);
      if (min == null || min <= 0n) errors.push("dest_min");
    }
    return { ok: errors.length === 0, errors: errors };
  }

  function fetchJson(url, timeoutMs) {
    timeoutMs = timeoutMs || 8000;
    var c = new AbortController();
    var tm = setTimeout(function () { c.abort(); }, timeoutMs);
    return fetch(url, { cache: "no-store", signal: c.signal, headers: { Accept: "application/json" } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (body) { return { ok: r.ok, status: r.status, body: body }; }); })
      .finally(function () { clearTimeout(tm); });
  }

  function fetchAccount(network, address) {
    if (!isValidAddress(address)) {
      return Promise.resolve({ ok: false, error: "invalid_address" });
    }
    var addr = address.trim();
    return fetchJson(accountUrl(network, addr)).then(function (res) {
      if (res.status === 404) return { ok: false, error: "not_found", status: 404, address: addr };
      if (!res.ok) return { ok: false, error: "http", status: res.status, address: addr };
      var account = res.body;
      return {
        ok: true,
        address: addr,
        account: account,
        balances: parseBalances(account, { usdcIssuer: usdcIssuer(network) }),
        homeDomain: account.home_domain || "",
        spendableXlm: spendableXlm(account)
      };
    }).catch(function (e) {
      return { ok: false, error: e && e.name === "AbortError" ? "timeout" : "network", address: addr };
    });
  }

  function fetchXlmUsd() {
    try {
      var tape = root.G6_TAPE && root.G6_TAPE.data && root.G6_TAPE.data.XLM;
      if (tape && tape.px > 0) return Promise.resolve({ px: tape.px, src: tape.src || "tape" });
    } catch (e) {}
    return fetchJson("https://api.exchange.coinbase.com/products/XLM-USD/ticker", 6000).then(function (res) {
      var px = res.ok ? parseFloat(res.body.price || res.body.last) : NaN;
      if (px > 0) return { px: px, src: "Coinbase Exchange" };
      return fetchJson("https://api.kraken.com/0/public/Ticker?pair=XLMUSD", 6000).then(function (kr) {
        var k = kr.ok && kr.body && kr.body.result && Object.values(kr.body.result)[0];
        var p2 = k && parseFloat(k.c && k.c[0]);
        if (p2 > 0) return { px: p2, src: "Kraken" };
        return { px: null, src: null };
      });
    }).catch(function () { return { px: null, src: null }; });
  }


  function isOperableSource(source) {
    return source === "wallet" || source === "key";
  }

  function sourceStatusLabel(source) {
    if (source === "key") return "Signed in";
    if (source === "wallet") return "Connected";
    return "Looked up";
  }

  function sourceCaption(source) {
    if (source === "key") {
      return "Signed in with an Alpha Hive account. Balances are shown only; the virtual trading balance is unchanged.";
    }
    if (source === "wallet") {
      return "Connected wallet. Balances are shown only; the virtual trading balance is unchanged.";
    }
    return "Read-only lookup (not connected). Balances are shown only; the virtual trading balance is unchanged.";
  }

  function convertActionLabel(keySession) {
    return keySession ? "Sign and submit" : "Sign convert in wallet";
  }

  function explorerTxUrl(network, hash) {
    if (!hash || !/^[0-9a-fA-F]{8,128}$/.test(String(hash))) return "";
    var net = network === "testnet" ? "testnet" : "public";
    return "https://stellar.expert/explorer/" + net + "/tx/" + hash;
  }

  function txSuccessLinkLabel(hash) {
    var h = String(hash || "");
    if (h.length < 8) return h;
    return "View on Stellar Expert (" + h.slice(0, 8) + "\u2026)";
  }

  function paintSubmitSuccess(el, okMsg, hash, network) {
    if (!el) return;
    el.textContent = "";
    var text = document.createTextNode((okMsg || "Submitted.") + (hash ? " " : ""));
    el.appendChild(text);
    var href = explorerTxUrl(network, hash);
    if (href) {
      var a = document.createElement("a");
      a.href = href;
      a.textContent = txSuccessLinkLabel(hash);
      a.rel = "noopener noreferrer";
      a.target = "_blank";
      el.appendChild(a);
    } else if (hash) {
      el.appendChild(document.createTextNode("Hash " + hash));
    }
  }

  var api = {
    USDC_ISSUER: USDC_ISSUER,
    USDC_ISSUER_TESTNET: USDC_ISSUER_TESTNET,
    SDF_ACCOUNT: SDF_ACCOUNT,
    NETWORKS: NETWORKS,
    MIN_SIM_USD: MIN_SIM_USD,
    BASE_RESERVE_XLM: BASE_RESERVE_XLM,
    DEFAULT_SLIPPAGE_PCT: DEFAULT_SLIPPAGE_PCT,
    MAX_SLIPPAGE_PCT: MAX_SLIPPAGE_PCT,
    FEE_CUSHION_XLM: FEE_CUSHION_XLM,
    horizonUrl: horizonUrl,
    networkPassphrase: networkPassphrase,
    accountUrl: accountUrl,
    usdcIssuer: usdcIssuer,
    isValidAddress: isValidAddress,
    shortenAddress: shortenAddress,
    parseBalances: parseBalances,
    usdValue: usdValue,
    formatAmount: formatAmount,
    formatUsd: formatUsd,
    isExchangeDomain: isExchangeDomain,
    memoAdvice: memoAdvice,
    chooseMemo: chooseMemo,
    validatePayment: validatePayment,
    canSetSimBalance: canSetSimBalance,
    applySimStartingBalance: applySimStartingBalance,
    toStroops: toStroops,
    fromStroops: fromStroops,
    normalizeAmount: normalizeAmount,
    minReceived: minReceived,
    reserveXlm: reserveXlm,
    spendableXlm: spendableXlm,
    hasUsdcTrustline: hasUsdcTrustline,
    formatRoute: formatRoute,
    strictSendUrl: strictSendUrl,
    parseStrictSendQuote: parseStrictSendQuote,
    validateConvert: validateConvert,
    fetchAccount: fetchAccount,
    fetchXlmUsd: fetchXlmUsd,
    mount: mount,
    getAddress: function () { return (state && state.address) || ""; },
    isConnected: function () { return !!(state && state.connected); },
    disconnect: function () { if (typeof onDisconnect === "function") onDisconnect(); },
    applyKeySession: applyKeySession,
    isOperableSource: isOperableSource,
    sourceStatusLabel: sourceStatusLabel,
    sourceCaption: sourceCaption,
    convertActionLabel: convertActionLabel,
    explorerTxUrl: explorerTxUrl,
    txSuccessLinkLabel: txSuccessLinkLabel,
    paintSubmitSuccess: paintSubmitSuccess
  };

  var mounted = false;
  var state = {
    network: "public",
    connected: false,
    address: "",
    source: "", /* "wallet" | "lookup" */
    balances: null,
    homeDomain: "",
    usd: null,
    xlmPx: null,
    xlmSrc: "",
    destHomeDomain: "",
    timer: null,
    kitReady: false,
    account: null,
    spendable: 0,
    quote: null,
    quoteSeq: 0,
    convertTimer: null
  };

  function $(id) { return document.getElementById(id); }
  function kit() { return root.AlphaHiveStellarKit; }

  function ensureKit() {
    var K = kit();
    if (!K || !K.StellarWalletsKit) throw new Error("Stellar Wallets Kit did not load.");
    if (!state.kitReady) {
      K.StellarWalletsKit.init({
        modules: K.defaultModules(),
        network: state.network === "testnet" ? K.Networks.TESTNET : K.Networks.PUBLIC,
        theme: K.hiveTheme || K.SwkAppDarkTheme
      });
      if (K.KitEventType && typeof K.StellarWalletsKit.on === "function") {
        K.StellarWalletsKit.on(K.KitEventType.DISCONNECT, function () {
          clearWallet(false);
        });
        K.StellarWalletsKit.on(K.KitEventType.STATE_UPDATED, function (ev) {
          var addr = ev && ev.payload && ev.payload.address;
          if (addr && isValidAddress(addr) && addr !== state.address) {
            setConnected(addr);
          }
        });
      }
      state.kitReady = true;
    }
    K.StellarWalletsKit.setNetwork(state.network === "testnet" ? K.Networks.TESTNET : K.Networks.PUBLIC);
    return K;
  }

  function setStat(kind, text) {
    var el = $("stellarStat");
    if (!el) return;
    el.className = "stellar-stat" + (kind ? " " + kind : "");
    el.textContent = text;
  }

  function renderBalances() {
    var box = $("stellarBalances");
    if (!box) return;
    if (!state.address || !state.balances) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    $("stellarAddr").textContent = shortenAddress(state.address);
    $("stellarAddr").title = state.address;
    $("stellarAddrFull").textContent = state.address;
    $("stellarXlm").textContent = formatAmount(state.balances.xlm) + " XLM";
    $("stellarUsdc").textContent = formatAmount(state.balances.usdc) + " USDC";
    var usdEl = $("stellarUsd");
    if (state.usd && state.usd.priced) {
      var tag = state.network === "testnet" ? " (indicative; testnet has no cash value)" : "";
      usdEl.textContent = formatUsd(state.usd.usd) + tag;
    } else {
      usdEl.textContent = "USD value unavailable";
    }
    var srcEl = $("stellarSource");
    var feed = state.xlmSrc ? " XLM-USD from " + state.xlmSrc + "." : "";
    srcEl.textContent = sourceCaption(state.source) + feed;
    var ul = $("stellarOther");
    ul.innerHTML = "";
    (state.balances.others || []).forEach(function (row) {
      var li = document.createElement("li");
      var left = document.createElement("span");
      left.textContent = row.pool ? "LP share" : (row.code + (row.issuer ? " (" + shortenAddress(row.issuer, 4, 4) + ")" : ""));
      var right = document.createElement("span");
      right.textContent = formatAmount(row.balance);
      li.appendChild(left); li.appendChild(right);
      ul.appendChild(li);
    });
    $("stellarOtherWrap").hidden = !state.balances.others.length;
    var useBtn = $("stellarUseBal");
    var canUse = state.source === "wallet" && state.network === "public" && state.usd && canSetSimBalance(state.usd.usd);
    useBtn.hidden = !canUse;
    useBtn.disabled = !canUse || ($("inBal") && $("inBal").disabled);
    useBtn.title = "Copies the approximate USD value into the starting virtual balance. The Stellar wallet is not charged.";
    $("stellarDisconnect").hidden = !isOperableSource(state.source);
    $("stellarSend").hidden = !isOperableSource(state.source);
    if ($("stellarConvert")) $("stellarConvert").hidden = !isOperableSource(state.source);
    renderLiveBalance();
  }

  function renderLiveBalance() {
    var box = $("stellarLiveBox");
    if (!box) return;
    var show = isOperableSource(state.source) && state.address && state.balances;
    box.hidden = !show;
    if (!show) return;
    var usdcEl = $("stellarLiveUsdc");
    var noteEl = $("stellarLiveNote");
    var issuer = usdcIssuer(state.network);
    var hasTl = hasUsdcTrustline(state.account || state.balances, issuer);
    var amt = state.balances.usdc;
    var test = state.network === "testnet";
    if (usdcEl) {
      if (!hasTl) usdcEl.textContent = "No USDC trustline yet";
      else usdcEl.textContent = formatAmount(amt) + (test ? " USDC (test)" : " USDC");
    }
    if (noteEl) {
      noteEl.textContent = test
        ? "Read-only for now. Testnet USDC has no cash value and is not used for trading yet."
        : "Read-only for now. This USDC is not used for trading yet. Live trades will use Stellar DEX pairs only.";
    }
  }

  function canSign() {
    return !!state.address && (state.source === "wallet" || state.source === "key");
  }

  function keyActive() {
    var S = root.AlphaHiveKeySession;
    return !!(S && S.isSignedIn() && S.address() && S.address() === state.address);
  }

  function confirmKeySign(details) {
    var net = state.network === "testnet" ? "TESTNET" : "PUBLIC (real funds)";
    return window.confirm(
      "Sign this " + net + " transaction with your in-memory Alpha Hive account key?\n\n" +
      "From: " + state.address + "\n" +
      (details || "") + "\n\n" +
      "The key stays in memory only and is not stored."
    );
  }

  function clearWallet(disconnectKit) {
    state.connected = false;
    if (state.source === "wallet" || state.source === "key") {
      state.address = "";
      state.source = "";
      state.balances = null;
      state.homeDomain = "";
      state.usd = null;
    }
    $("stellarConnect").hidden = false;
    $("stellarDisconnect").hidden = true;
    $("stellarSend").hidden = true;
    if ($("stellarConvert")) $("stellarConvert").hidden = true;
    if (!state.address) {
      setStat("", "Not connected");
      renderBalances();
    }
    if (disconnectKit && state.kitReady) {
      try { kit().StellarWalletsKit.disconnect(); } catch (e) {}
    }
  }

  function setConnected(address, source) {
    source = source || "wallet";
    if (source === "wallet" && root.AlphaHiveKeySession && root.AlphaHiveKeySession.isSignedIn()) {
      try { root.AlphaHiveKeySession.logout(); } catch (e) {}
    }
    state.connected = true;
    state.address = address;
    state.source = source;
    $("stellarConnect").hidden = source === "key" ? false : true;
    $("stellarDisconnect").hidden = false;
    $("stellarSend").hidden = false;
    if ($("stellarConvert")) $("stellarConvert").hidden = false;
    setStat("ok", (source === "key" ? "Signed in \u00b7 " : "Connected \u00b7 ") + shortenAddress(address));
    return refreshShown();
  }

  function applyKeySession() {
    var S = root.AlphaHiveKeySession;
    if (!S || !S.isSignedIn()) return Promise.resolve();
    state.network = S.network() === "public" ? "public" : "testnet";
    if ($("stellarTestnet")) $("stellarTestnet").checked = state.network === "testnet";
    var warn = $("stellarNetWarn");
    if (warn) warn.hidden = state.network !== "public";
    return setConnected(S.address(), "key");
  }

  function refreshShown() {
    if (!state.address) return Promise.resolve();
    setStat(isOperableSource(state.source) ? "ok" : "", "Loading balances\u2026");
    return Promise.all([fetchAccount(state.network, state.address), fetchXlmUsd()]).then(function (pair) {
      var acc = pair[0], px = pair[1];
      state.xlmPx = px.px;
      state.xlmSrc = px.src || "";
      if (!acc.ok) {
        state.balances = null;
        state.account = null;
        state.spendable = 0;
        state.usd = null;
        var why = acc.error === "not_found"
          ? "No account on " + (NETWORKS[state.network] || NETWORKS.public).label + " for " + shortenAddress(state.address) + ". A new Stellar account does not exist on-chain until it is funded with XLM (about 1 XLM plus 0.5 XLM per trustline). On testnet, use Fund with Friendbot. On Public, send XLM to this address or use the Fund your account panel."
          : "Could not read balances (" + (acc.error || "error") + ")";
        setStat("err", why);
        renderBalances();
        return acc;
      }
      state.balances = acc.balances;
      state.account = acc.account || null;
      state.spendable = acc.spendableXlm || 0;
      state.homeDomain = acc.homeDomain;
      state.usd = usdValue(acc.balances, px.px);
      setStat(isOperableSource(state.source) ? "ok" : "", sourceStatusLabel(state.source) + " \u00b7 " + shortenAddress(state.address));
      renderBalances();
      return acc;
    });
  }

  function onConnect() {
    try {
      var K = ensureKit();
      setStat("warn", "Choose a wallet in the connect modal\u2026");
      return K.StellarWalletsKit.authModal().then(function (res) {
        var addr = res && res.address;
        if (!addr) throw new Error("No address returned.");
        return setConnected(addr);
      }).catch(function (err) {
        var msg = (err && (err.message || err.toString())) || "Connection cancelled.";
        if (/closed the modal/i.test(msg)) setStat("", "Not connected");
        else setStat("err", msg);
      });
    } catch (e) {
      setStat("err", e.message || String(e));
    }
  }

  function onDisconnect() {
    if (state.source === "key" && root.AlphaHiveKeySession && root.AlphaHiveKeySession.isSignedIn()) {
      try { root.AlphaHiveKeySession.logout(); } catch (e) {}
    }
    clearWallet(true);
  }

  function onLookup(address) {
    var addr = (address || ($("stellarLookup") && $("stellarLookup").value) || "").trim();
    if (!isValidAddress(addr)) {
      setStat("err", "Enter a valid public address (it starts with G, 56 characters).");
      return Promise.resolve();
    }
    if ($("stellarLookup")) $("stellarLookup").value = addr;
    state.address = addr;
    state.source = "lookup";
    state.connected = false;
    $("stellarConnect").hidden = false;
    $("stellarDisconnect").hidden = true;
    $("stellarSend").hidden = true;
    if ($("stellarConvert")) $("stellarConvert").hidden = true;
    return refreshShown();
  }

  function onUseBal() {
    if (!state.usd || !state.usd.priced) return;
    var res = applySimStartingBalance(state.usd.usd, document);
    if (res.ok) setStat("ok", "Starting virtual balance set to " + formatUsd(res.value) + ". The Stellar wallet was not charged.");
    else if (res.error === "session_running") setStat("warn", "Stop the demo session first, then set the starting virtual balance.");
    else if (res.error === "too_small") setStat("warn", "USD value is below the $100 minimum for a starting virtual balance.");
    else setStat("err", "Could not set the starting virtual balance.");
  }

  function fillSendAssets() {
    var sel = $("stellarAsset");
    if (!sel) return;
    sel.innerHTML = "";
    var list = (state.balances && state.balances.sendable) || [];
    if (!list.length) list = [{ code: "XLM", issuer: null, native: true, balance: 0 }];
    list.forEach(function (row, i) {
      var opt = document.createElement("option");
      opt.value = row.native ? "XLM" : row.code + ":" + row.issuer;
      opt.textContent = row.code + " \u00b7 " + formatAmount(row.balance) + " available";
      opt.selected = i === 0 && (row.native || row.usdc);
      sel.appendChild(opt);
    });
  }

  function updateMemoWarn() {
    var dest = ($("stellarDest") && $("stellarDest").value) || "";
    var memo = ($("stellarMemo") && $("stellarMemo").value) || "";
    var known = isValidAddress(dest) && state.destHomeDomain && isExchangeDomain(state.destHomeDomain)
      ? true
      : false;
    /* If dest typed but home domain not yet fetched, still show the general warning. */
    var advice = memoAdvice({ memo: memo, knownExchange: known, homeDomain: state.destHomeDomain });
    var el = $("stellarMemoWarn");
    if (!el) return;
    el.hidden = false;
    el.textContent = advice.message;
    el.classList.toggle("strong", advice.warnExchange);
  }

  function lookupDestDomain() {
    var dest = ($("stellarDest") && $("stellarDest").value || "").trim();
    state.destHomeDomain = "";
    if (!isValidAddress(dest)) { updateMemoWarn(); return; }
    fetchAccount(state.network, dest).then(function (acc) {
      if (acc.ok) state.destHomeDomain = acc.homeDomain || "";
      updateMemoWarn();
    });
  }

  function openSend() {
    if (!canSign()) return;
    fillSendAssets();
    $("stellarDest").value = "";
    $("stellarAmt").value = "";
    $("stellarMemo").value = "";
    $("stellarSendMsg").textContent = "";
    if ($("stellarSendTitle")) {
      $("stellarSendTitle").textContent = state.source === "key" ? "Send from Alpha Hive account" : "Send from connected wallet";
    }
    if ($("stellarSendGo")) $("stellarSendGo").textContent = keyActive() ? "Sign and submit" : "Sign in wallet";
    $("stellarSendNet").textContent = "Network: " + (NETWORKS[state.network] || NETWORKS.public).label +
      " \u00b7 From " + shortenAddress(state.address);
    state.destHomeDomain = "";
    updateMemoWarn();
    var dlg = $("stellarSendDlg");
    dlg.hidden = false;
    dlg.classList.add("show");
    $("stellarDest").focus();
  }

  function closeSend() {
    var dlg = $("stellarSendDlg");
    if (!dlg) return;
    dlg.classList.remove("show");
    dlg.hidden = true;
  }

  function selectedAsset() {
    var raw = ($("stellarAsset") && $("stellarAsset").value) || "XLM";
    if (raw === "XLM") return { code: "XLM", issuer: null, native: true };
    var parts = raw.split(":");
    return { code: parts[0], issuer: parts.slice(1).join(":"), native: false };
  }

  function onSendGo() {
    var dest = ($("stellarDest") && $("stellarDest").value || "").trim();
    var amount = ($("stellarAmt") && $("stellarAmt").value || "").trim();
    var memoVal = ($("stellarMemo") && $("stellarMemo").value || "");
    var asset = selectedAsset();
    var check = validatePayment({ destination: dest, amount: amount, assetCode: asset.code, memo: memoVal });
    var msg = $("stellarSendMsg");
    if (!check.ok) {
      msg.textContent = check.errors.indexOf("memo_too_long") >= 0
        ? "Memo text is over 28 bytes. Shorten it, or use a numeric memo id."
        : "Check the destination, amount and asset.";
      return;
    }
    var advice = memoAdvice({ memo: memoVal, knownExchange: isExchangeDomain(state.destHomeDomain), homeDomain: state.destHomeDomain });
    if (advice.warnExchange && advice.missingMemo) {
      msg.textContent = advice.message;
      return;
    }
    if (advice.missingMemo) {
      var proceed = window.confirm("No memo entered. If this address belongs to an exchange or hosted wallet, your funds may be lost without a memo. Sign anyway?");
      if (!proceed) return;
    }
    var K = kit();
    if (!K || !K.Horizon || !K.TransactionBuilder) {
      msg.textContent = "The Stellar toolkit did not load, so a payment cannot be built.";
      return;
    }
    msg.textContent = "Building payment\u2026";
    var passphrase = networkPassphrase(state.network);
    var server = new K.Horizon.Server(horizonUrl(state.network));
    server.loadAccount(state.address).then(function (account) {
      return server.fetchBaseFee().then(function (fee) {
        var sdkAsset = asset.native ? K.Asset.native() : new K.Asset(asset.code, asset.issuer);
        var builder = new K.TransactionBuilder(account, { fee: String(fee), networkPassphrase: passphrase });
        builder.addOperation(K.Operation.payment({ destination: dest, asset: sdkAsset, amount: String(amount) }));
        if (check.memo.type === "id") builder.addMemo(K.Memo.id(check.memo.value));
        else if (check.memo.type === "text") builder.addMemo(K.Memo.text(check.memo.value));
        builder.setTimeout(180);
        var tx = builder.build();
        if (keyActive()) {
          var sendBits = "Send " + amount + " " + asset.code + " to " + dest;
          if (memoVal) sendBits += "\nMemo: " + memoVal;
          if (!confirmKeySign(sendBits)) {
            msg.textContent = "Signature cancelled.";
            return null;
          }
          msg.textContent = "Signing\u2026";
          var signedXdr = root.AlphaHiveKeySession.signXDR(tx.toXDR(), passphrase);
          var signedTx = new K.Transaction(signedXdr, passphrase);
          msg.textContent = "Submitting to Horizon\u2026";
          return server.submitTransaction(signedTx);
        }
        msg.textContent = "Waiting for your wallet to sign\u2026";
        return K.StellarWalletsKit.signTransaction(tx.toXDR(), { address: state.address, networkPassphrase: passphrase }).then(function (signed) {
          var xdr = signed && signed.signedTxXdr;
          if (!xdr) throw new Error("Wallet did not return a signed transaction.");
          var signedTx = new K.Transaction(xdr, passphrase);
          msg.textContent = "Submitting to Horizon\u2026";
          return server.submitTransaction(signedTx);
        });
      });
    }).then(function (res) {
      if (!res) return;
      var hash = res.hash || res.id;
      return refreshShown().then(function () {
        paintSubmitSuccess(msg, "Submitted.", hash, state.network);
      });
    }).catch(function (err) {
      var text = (err && (err.message || err.reason || err.toString())) || "Send failed.";
      if (/closed the modal|User declined|rejected|denied/i.test(text)) text = "Signature cancelled in the wallet.";
      msg.textContent = text;
    });
  }

  function usdcLabel(network) {
    return network === "testnet" ? "USDC (test)" : "USDC";
  }

  function closeConvert() {
    var dlg = $("stellarConvertDlg");
    if (!dlg) return;
    dlg.classList.remove("show");
    dlg.hidden = true;
    if (state.convertTimer) { clearTimeout(state.convertTimer); state.convertTimer = null; }
  }

  function updateTrustUi() {
    var issuer = usdcIssuer(state.network);
    var has = hasUsdcTrustline(state.account || state.balances, issuer);
    var warn = $("stellarTrustWarn");
    var trustBtn = $("stellarTrustGo");
    var go = $("stellarConvertGo");
    if (warn) {
      warn.hidden = has;
      warn.textContent = has ? "" :
        "This account has no " + usdcLabel(state.network) + " trustline yet. Add one (your wallet will sign) before converting.";
    }
    if (trustBtn) trustBtn.hidden = has;
    if (go) go.disabled = !has;
    return has;
  }

  function renderQuote() {
    var box = $("stellarQuote");
    if (!box) return;
    var q = state.quote;
    if (!q || !q.ok) { box.hidden = true; return; }
    box.hidden = false;
    var test = state.network === "testnet";
    var tag = test ? " (test)" : "";
    if ($("stellarQuoteOut")) $("stellarQuoteOut").textContent = formatAmount(q.destAmount) + " USDC" + tag;
    if ($("stellarQuoteRoute")) $("stellarQuoteRoute").textContent = q.route;
    if ($("stellarQuoteMin")) $("stellarQuoteMin").textContent = formatAmount(q.destMin) + " USDC" + tag +
      " (slippage " + String(q.slippagePct) + "%)";
  }

  function scheduleQuote() {
    if (state.convertTimer) clearTimeout(state.convertTimer);
    state.convertTimer = setTimeout(runQuote, 400);
  }

  function runQuote() {
    var amtRaw = ($("stellarConvertAmt") && $("stellarConvertAmt").value || "").trim();
    var slipRaw = ($("stellarSlippage") && $("stellarSlippage").value) || String(DEFAULT_SLIPPAGE_PCT);
    var msg = $("stellarConvertMsg");
    var box = $("stellarQuote");
    state.quote = null;
    var amt = toStroops(amtRaw);
    if (amt == null || amt <= 0n) {
      if (box) box.hidden = true;
      return Promise.resolve();
    }
    var check = validateConvert({ sendAmount: amtRaw, slippagePct: slipRaw, spendableXlm: state.spendable });
    if (check.errors.indexOf("slippage") >= 0) {
      if (box) box.hidden = true;
      if (msg) msg.textContent = "Slippage must be between 0 and " + MAX_SLIPPAGE_PCT + "%.";
      return Promise.resolve();
    }
    if (msg) msg.textContent = "Fetching quote\u2026";
    var seq = (state.quoteSeq = (state.quoteSeq || 0) + 1);
    var url = strictSendUrl(state.network, fromStroops(amt), usdcIssuer(state.network));
    return fetchJson(url, 8000).then(function (res) {
      if (seq !== state.quoteSeq) return;
      if (!res.ok) {
        state.quote = { ok: false, error: "http" };
        if (box) box.hidden = true;
        if (msg) msg.textContent = "Could not fetch a quote from Horizon.";
        return;
      }
      var q = parseStrictSendQuote(res.body, slipRaw);
      state.quote = q;
      renderQuote();
      if (!q.ok) {
        if (msg) msg.textContent = "No path found to " + usdcLabel(state.network) + " for that amount. Try a different size.";
      } else if (check.errors.indexOf("spendable") >= 0) {
        if (msg) msg.textContent = "Amount is above the spendable XLM. The 0.5 XLM reserve must stay in the account.";
      } else if (msg) {
        msg.textContent = "";
      }
    }).catch(function () {
      if (seq !== state.quoteSeq) return;
      state.quote = { ok: false, error: "network" };
      if (box) box.hidden = true;
      if (msg) msg.textContent = "Could not fetch a quote from Horizon.";
    });
  }

  function openConvert() {
    if (!canSign()) return;
    if ($("stellarConvertAmt")) $("stellarConvertAmt").value = "";
    if ($("stellarSlippage")) $("stellarSlippage").value = String(DEFAULT_SLIPPAGE_PCT);
    if ($("stellarConvertMsg")) $("stellarConvertMsg").textContent = "";
    state.quote = null;
    renderQuote();
    var net = NETWORKS[state.network] || NETWORKS.public;
    var spendable = formatAmount(state.spendable);
    if ($("stellarConvertNet")) {
      $("stellarConvertNet").textContent = "Network: " + net.label +
        " \u00b7 From " + shortenAddress(state.address) +
        " \u00b7 Spendable " + spendable + " XLM";
    }
    if ($("stellarConvertSpendable")) {
      $("stellarConvertSpendable").textContent = "Spendable: " + spendable +
        " XLM (XLM balance minus the Stellar reserve and a small fee cushion).";
    }
    if ($("stellarConvertReserve")) {
      $("stellarConvertReserve").textContent = "A Stellar account must keep a reserve of 0.5 XLM per entry (the account itself holds 1 XLM). Don't convert your entire XLM balance or the account can become unusable.";
    }
    if ($("stellarConvertTitle")) {
      $("stellarConvertTitle").textContent = state.network === "testnet"
        ? "Convert XLM to USDC (testnet)"
        : "Convert XLM to USDC";
    }
    if ($("stellarConvertGo")) $("stellarConvertGo").textContent = convertActionLabel(keyActive());
    updateTrustUi();
    var dlg = $("stellarConvertDlg");
    if (!dlg) return;
    dlg.hidden = false;
    dlg.classList.add("show");
    if ($("stellarConvertAmt")) $("stellarConvertAmt").focus();
  }

  function onUseMaxConvert() {
    if (state.spendable == null) return;
    var cap = toStroops(state.spendable);
    if (cap == null || cap <= 0n) return;
    if ($("stellarConvertAmt")) $("stellarConvertAmt").value = fromStroops(cap);
    runQuote();
  }

  function signAndSubmit(operations, pendingMsg, okMsg, details) {
    signAndSubmit._details = details || "";
    var msg = $("stellarConvertMsg");
    var K = kit();
    if (!K || !K.Horizon || !K.TransactionBuilder) {
      if (msg) msg.textContent = "The Stellar toolkit did not load, so the transaction cannot be built.";
      return Promise.resolve();
    }
    if (msg) msg.textContent = pendingMsg || "Building transaction\u2026";
    var passphrase = networkPassphrase(state.network);
    var server = new K.Horizon.Server(horizonUrl(state.network));
    return server.loadAccount(state.address).then(function (account) {
      return server.fetchBaseFee().then(function (fee) {
        var builder = new K.TransactionBuilder(account, { fee: String(fee), networkPassphrase: passphrase });
        for (var i = 0; i < operations.length; i++) builder.addOperation(operations[i]);
        builder.setTimeout(180);
        var tx = builder.build();
        if (keyActive()) {
          if (!confirmKeySign(signAndSubmit._details || "Review the transaction details above.")) {
            if (msg) msg.textContent = "Signature cancelled.";
            return null;
          }
          if (msg) msg.textContent = "Signing\u2026";
          var signedXdr = root.AlphaHiveKeySession.signXDR(tx.toXDR(), passphrase);
          var signedTx = new K.Transaction(signedXdr, passphrase);
          if (msg) msg.textContent = "Submitting to Horizon\u2026";
          return server.submitTransaction(signedTx);
        }
        if (msg) msg.textContent = "Waiting for your wallet to sign\u2026";
        return K.StellarWalletsKit.signTransaction(tx.toXDR(), { address: state.address, networkPassphrase: passphrase }).then(function (signed) {
          var xdr = signed && signed.signedTxXdr;
          if (!xdr) throw new Error("Wallet did not return a signed transaction.");
          var signedTx = new K.Transaction(xdr, passphrase);
          if (msg) msg.textContent = "Submitting to Horizon\u2026";
          return server.submitTransaction(signedTx);
        });
      });
    }).then(function (res) {
      if (!res) return res;
      var hash = res.hash || res.id;
      return refreshShown().then(function () {
        paintSubmitSuccess(msg, okMsg || "Submitted.", hash, state.network);
        if ($("stellarConvertNet")) {
          $("stellarConvertNet").textContent = "Network: " + (NETWORKS[state.network] || NETWORKS.public).label +
            " \u00b7 From " + shortenAddress(state.address) +
            " \u00b7 Spendable " + formatAmount(state.spendable) + " XLM";
        }
        if ($("stellarConvertSpendable")) {
          $("stellarConvertSpendable").textContent = "Spendable: " + formatAmount(state.spendable) +
            " XLM (XLM balance minus the Stellar reserve and a small fee cushion).";
        }
        if ($("stellarConvertGo")) $("stellarConvertGo").textContent = convertActionLabel(keyActive());
        updateTrustUi();
        return res;
      });
    }).catch(function (err) {
      var t = (err && (err.message || err.reason || err.toString())) || "Transaction failed.";
      if (/closed the modal|User declined|rejected|denied/i.test(t)) t = "Signature cancelled in the wallet.";
      if (msg) msg.textContent = t;
    });
  }

  function onTrustlineGo() {
    var K = kit();
    if (!K || !K.Operation || !K.Asset) {
      if ($("stellarConvertMsg")) $("stellarConvertMsg").textContent = "The Stellar toolkit did not load.";
      return;
    }
    if (hasUsdcTrustline(state.account || state.balances, usdcIssuer(state.network))) {
      updateTrustUi();
      return;
    }
    var asset = new K.Asset("USDC", usdcIssuer(state.network));
    signAndSubmit(
      [K.Operation.changeTrust({ asset: asset })],
      "Building USDC trustline\u2026",
      "USDC trustline added.",
      "Add a USDC trustline on " + (state.network === "testnet" ? "TESTNET" : "PUBLIC") + "."
    );
  }

  function onConvertGo() {
    var amtRaw = ($("stellarConvertAmt") && $("stellarConvertAmt").value || "").trim();
    var slipRaw = ($("stellarSlippage") && $("stellarSlippage").value) || String(DEFAULT_SLIPPAGE_PCT);
    var msg = $("stellarConvertMsg");
    var has = updateTrustUi();
    var check = validateConvert({
      sendAmount: amtRaw,
      slippagePct: slipRaw,
      spendableXlm: state.spendable,
      requireTrustline: true,
      hasTrustline: has,
      quote: state.quote
    });
    if (!check.ok) {
      if (check.errors.indexOf("trustline") >= 0) {
        if (msg) msg.textContent = "Add a USDC trustline first. Your wallet will sign that step.";
      } else if (check.errors.indexOf("spendable") >= 0) {
        if (msg) msg.textContent = "Amount is above the spendable XLM. The 0.5 XLM reserve must stay in the account.";
      } else if (check.errors.indexOf("quote") >= 0 || check.errors.indexOf("dest_min") >= 0) {
        if (msg) msg.textContent = "Get a valid quote before converting.";
      } else if (check.errors.indexOf("slippage") >= 0) {
        if (msg) msg.textContent = "Slippage must be between 0 and " + MAX_SLIPPAGE_PCT + "%.";
      } else {
        if (msg) msg.textContent = "Enter a valid XLM amount to convert.";
      }
      return;
    }
    var K = kit();
    if (!K || !K.Operation || !K.Asset) {
      if (msg) msg.textContent = "The Stellar toolkit did not load.";
      return;
    }
    if (msg) msg.textContent = "Refreshing quote\u2026";
    runQuote().then(function () {
      var q = state.quote;
      var recheck = validateConvert({
        sendAmount: amtRaw,
        slippagePct: slipRaw,
        spendableXlm: state.spendable,
        requireTrustline: true,
        hasTrustline: hasUsdcTrustline(state.account || state.balances, usdcIssuer(state.network)),
        quote: q
      });
      if (!recheck.ok || !q || !q.ok) {
        if (msg && !msg.textContent) msg.textContent = "Quote is no longer valid. Adjust the amount and try again.";
        return;
      }
      var destAsset = new K.Asset("USDC", usdcIssuer(state.network));
      var path = (q.path || []).map(function (p) {
        return p.asset_type === "native" ? K.Asset.native() : new K.Asset(p.asset_code, p.asset_issuer);
      });
      var op = K.Operation.pathPaymentStrictSend({
        sendAsset: K.Asset.native(),
        sendAmount: normalizeAmount(amtRaw),
        destination: state.address,
        destAsset: destAsset,
        destMin: q.destMin,
        path: path
      });
      return signAndSubmit(
        [op],
        "Building path payment\u2026",
        "Converted to " + usdcLabel(state.network) + ". Live session balance updated.",
        "Convert " + normalizeAmount(amtRaw) + " XLM to " + usdcLabel(state.network) + ", min received " + q.destMin + "."
      );
    });
  }

  function onNetworkToggle() {
    var box = $("stellarTestnet");
    var test = box && box.checked;
    if (!test && root.AlphaHiveKeySession && root.AlphaHiveKeySession.isSignedIn()) {
      var ok = window.confirm("Switch to the Public Stellar network? This uses real funds. Only continue with a small dedicated trading account, never your main wallet.");
      if (!ok) {
        if (box) box.checked = true;
        return;
      }
      root.AlphaHiveKeySession.setNetwork("public");
    } else if (root.AlphaHiveKeySession && root.AlphaHiveKeySession.isSignedIn()) {
      root.AlphaHiveKeySession.setNetwork("testnet");
    }
    state.network = test ? "testnet" : "public";
    var warn = $("stellarNetWarn");
    if (warn) warn.hidden = !!test;
    if (state.kitReady) {
      try { ensureKit(); } catch (e) {}
    }
    closeConvert();
    if (state.address) refreshShown();
  }

  function mount() {
    if (typeof document === "undefined") return api;
    if (mounted) return api;
    if (!$("stellarBox")) return api;
    mounted = true;
    $("stellarConnect").addEventListener("click", onConnect);
    $("stellarDisconnect").addEventListener("click", onDisconnect);
    $("stellarSend").addEventListener("click", openSend);
    if ($("stellarConvert")) $("stellarConvert").addEventListener("click", openConvert);
    $("stellarLookupBtn").addEventListener("click", function () { onLookup(); });
    $("stellarLookupSdf").addEventListener("click", function () {
      if ($("stellarLookup")) $("stellarLookup").value = SDF_ACCOUNT;
      onLookup(SDF_ACCOUNT);
    });
    $("stellarLookup").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); onLookup(); }
    });
    $("stellarUseBal").addEventListener("click", onUseBal);
    $("stellarTestnet").addEventListener("change", onNetworkToggle);
    $("stellarSendCancel").addEventListener("click", closeSend);
    $("stellarSendGo").addEventListener("click", onSendGo);
    $("stellarSendDlg").addEventListener("click", function (ev) {
      if (ev.target === $("stellarSendDlg")) closeSend();
    });
    if ($("stellarConvertCancel")) $("stellarConvertCancel").addEventListener("click", closeConvert);
    if ($("stellarConvertGo")) $("stellarConvertGo").addEventListener("click", onConvertGo);
    if ($("stellarTrustGo")) $("stellarTrustGo").addEventListener("click", onTrustlineGo);
    if ($("stellarConvertMax")) $("stellarConvertMax").addEventListener("click", onUseMaxConvert);
    if ($("stellarConvertDlg")) $("stellarConvertDlg").addEventListener("click", function (ev) {
      if (ev.target === $("stellarConvertDlg")) closeConvert();
    });
    if ($("stellarConvertAmt")) {
      $("stellarConvertAmt").addEventListener("input", scheduleQuote);
      $("stellarConvertAmt").addEventListener("change", scheduleQuote);
    }
    if ($("stellarSlippage")) {
      $("stellarSlippage").addEventListener("input", scheduleQuote);
      $("stellarSlippage").addEventListener("change", scheduleQuote);
    }
    $("stellarDest").addEventListener("blur", lookupDestDomain);
    $("stellarDest").addEventListener("input", function () { state.destHomeDomain = ""; updateMemoWarn(); });
    $("stellarMemo").addEventListener("input", updateMemoWarn);
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") return;
      if ($("stellarConvertDlg") && $("stellarConvertDlg").classList.contains("show")) { closeConvert(); return; }
      if ($("stellarSendDlg") && $("stellarSendDlg").classList.contains("show")) closeSend();
    });
    try {
      if (kit() && kit().StellarWalletsKit) {
        ensureKit();
        kit().StellarWalletsKit.getAddress().then(function (res) {
          if (res && res.address && isValidAddress(res.address)) setConnected(res.address);
        }).catch(function () {});
      }
    } catch (e) {}
    state.timer = setInterval(function () { if (state.address) refreshShown(); }, REFRESH_MS);
    return api;
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { mount(); });
    else mount();
  }

  return api;
});
