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
  var SDF_ACCOUNT = "GB6NVEN5HSUBKMYCE5ZOWSK5K23TBWRUQLZY3KNMXUZ3AQ2ESC4MY4AQ";
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
  function parseBalances(account) {
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
      if (code === "USDC" && issuer === USDC_ISSUER) {
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
        balances: parseBalances(account),
        homeDomain: account.home_domain || ""
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

  var api = {
    USDC_ISSUER: USDC_ISSUER,
    SDF_ACCOUNT: SDF_ACCOUNT,
    NETWORKS: NETWORKS,
    MIN_SIM_USD: MIN_SIM_USD,
    horizonUrl: horizonUrl,
    networkPassphrase: networkPassphrase,
    accountUrl: accountUrl,
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
    fetchAccount: fetchAccount,
    fetchXlmUsd: fetchXlmUsd,
    mount: mount
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
    kitReady: false
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
    srcEl.textContent = (state.source === "wallet"
      ? "Connected wallet. Balances are shown only; the virtual trading balance is unchanged."
      : "Read-only lookup (not connected). Balances are shown only; the virtual trading balance is unchanged.") + feed;
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
    $("stellarDisconnect").hidden = state.source !== "wallet";
    $("stellarSend").hidden = state.source !== "wallet";
  }

  function clearWallet(disconnectKit) {
    state.connected = false;
    if (state.source === "wallet") {
      state.address = "";
      state.source = "";
      state.balances = null;
      state.homeDomain = "";
      state.usd = null;
    }
    $("stellarConnect").hidden = false;
    $("stellarDisconnect").hidden = true;
    $("stellarSend").hidden = true;
    if (!state.address) {
      setStat("", "Not connected");
      renderBalances();
    }
    if (disconnectKit && state.kitReady) {
      try { kit().StellarWalletsKit.disconnect(); } catch (e) {}
    }
  }

  function setConnected(address) {
    state.connected = true;
    state.address = address;
    state.source = "wallet";
    $("stellarConnect").hidden = true;
    $("stellarDisconnect").hidden = false;
    $("stellarSend").hidden = false;
    setStat("ok", "Connected \u00b7 " + shortenAddress(address));
    return refreshShown();
  }

  function refreshShown() {
    if (!state.address) return Promise.resolve();
    setStat(state.source === "wallet" ? "ok" : "", "Loading balances\u2026");
    return Promise.all([fetchAccount(state.network, state.address), fetchXlmUsd()]).then(function (pair) {
      var acc = pair[0], px = pair[1];
      state.xlmPx = px.px;
      state.xlmSrc = px.src || "";
      if (!acc.ok) {
        state.balances = null;
        state.usd = null;
        var why = acc.error === "not_found"
          ? "No account on " + (NETWORKS[state.network] || NETWORKS.public).label + " for " + shortenAddress(state.address)
          : "Could not read balances (" + (acc.error || "error") + ")";
        setStat("err", why);
        renderBalances();
        return acc;
      }
      state.balances = acc.balances;
      state.homeDomain = acc.homeDomain;
      state.usd = usdValue(acc.balances, px.px);
      setStat(state.source === "wallet" ? "ok" : "", (state.source === "wallet" ? "Connected" : "Looked up") + " \u00b7 " + shortenAddress(state.address));
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
    if (state.source !== "wallet" || !state.address) return;
    fillSendAssets();
    $("stellarDest").value = "";
    $("stellarAmt").value = "";
    $("stellarMemo").value = "";
    $("stellarSendMsg").textContent = "";
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
      var hash = res && (res.hash || res.id);
      msg.textContent = hash ? "Submitted. Hash " + hash : "Submitted.";
      refreshShown();
    }).catch(function (err) {
      var text = (err && (err.message || err.reason || err.toString())) || "Send failed.";
      if (/closed the modal|User declined|rejected|denied/i.test(text)) text = "Signature cancelled in the wallet.";
      msg.textContent = text;
    });
  }

  function onNetworkToggle() {
    var test = $("stellarTestnet") && $("stellarTestnet").checked;
    state.network = test ? "testnet" : "public";
    if (state.kitReady) {
      try { ensureKit(); } catch (e) {}
    }
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
    $("stellarDest").addEventListener("blur", lookupDestDomain);
    $("stellarDest").addEventListener("input", function () { state.destHomeDomain = ""; updateMemoWarn(); });
    $("stellarMemo").addEventListener("input", updateMemoWarn);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && $("stellarSendDlg") && $("stellarSendDlg").classList.contains("show")) closeSend();
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
