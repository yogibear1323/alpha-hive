/**
 * Alpha Hive Stellar DEX market adapter.
 *
 * Live pair prices, 24h change/volume, and order books come from the official
 * Stellar Horizon API (horizon.stellar.org). StellarChain.io is used only for
 * clickable "view on StellarChain" links: its public market snapshots were last
 * updated 18 Nov 2026, volumes look unusable, it has no order-book endpoint,
 * and browser calls from GitHub Pages do not receive Access-Control-Allow-Origin.
 *
 * Works in the browser and in Node (module.exports) so tests can mock fetch.
 * Never invents prices: a failed or empty response is marked unavailable.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.StellarDexMarkets = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  var HORIZON = "https://horizon.stellar.org";
  var STELLARCHAIN_ASSET = "https://stellarchain.io/assets/";
  var REFRESH_MS = 20000;
  var BOOK_LIMIT = 10;
  var HOUR_MS = 3600000;
  var DAY_MS = 24 * 60 * 60 * 1000;
  var SOURCE_ID = "horizon";
  var SOURCE_LABEL = "Stellar Horizon (live on-network)";
  var SUBSTITUTION_REASON =
    "StellarChain.io has a public market-asset API at api.stellarchain.dev, but it is not used as a live source: snapshots were last updated 18 Nov 2026, there is no order-book endpoint, and responses lack Access-Control-Allow-Origin so a GitHub Pages browser app cannot read them.";

  var USDC = {
    code: "USDC",
    issuer: "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN",
    domain: "circle.com",
    name: "USD Coin"
  };
  var EURC = {
    code: "EURC",
    issuer: "GDHU6WRG4IEQXM5NZ4BMPKOXHW76MZM4Y2IEMFDVXBSDP6SJY4ITNPP2",
    domain: "circle.com",
    name: "EURC (Circle)"
  };
  var SHX = {
    code: "SHX",
    issuer: "GDSTRSHXHGJ7ZIVRBXEYE5Q74XUVCUSEKEBR7UCHEUUEK72N7I7KJ6JH",
    domain: "stronghold.co",
    name: "Stronghold SHx"
  };
  var YXLM = {
    code: "yXLM",
    issuer: "GARDNV3Q7YGT4AKSDF25LT32YSCCW4EV22Y2TV3I2PU2MMXJTEDL5T55",
    domain: "ultracapital.xyz",
    name: "yXLM"
  };
  var AQUA = {
    code: "AQUA",
    issuer: "GBNZILSTVQZ4R7IKQDGHYGY2QXL5QOFJYQMXPKWRRM5PAV7Y4M67AQUA",
    domain: "aqua.network",
    name: "AQUA"
  };
  var PYUSD = {
    code: "PYUSD",
    issuer: "GDQE7IXJ4HUHV6RQHIUPRJSEZE4DRS5WY577O2FY6YQ5LVWZ7JZTU2V5",
    domain: "token-metadata.paxos.com",
    name: "PayPal USD"
  };
  var YUSDC = {
    code: "yUSDC",
    issuer: "GDGTVWSM4MGS4T7Z6W4RPWOCHE2I6RDFCIFZGS3DOA63LWQTRNZNTTFF",
    domain: "ultracapital.xyz",
    name: "yUSDC"
  };
  var XLM = { code: "XLM", issuer: null, domain: "stellar.org", name: "Lumens", native: true };

  function pair(id, base, counter) {
    return {
      id: id,
      base: base,
      counter: counter,
      label: base.code + "/" + counter.code
    };
  }

  // Official issuers from StellarExpert's free Top 50 list (2026-11-27).
  // Circle EURC is used; the Mykobo EURC (GAQRF3…WDXZ7NM) is not listed.
  var PAIRS = [
    pair("XLM-USDC", XLM, USDC),
    pair("EURC-USDC", EURC, USDC),
    pair("XLM-EURC", XLM, EURC),
    pair("SHX-XLM", SHX, XLM),
    pair("yXLM-XLM", YXLM, XLM),
    pair("AQUA-XLM", AQUA, XLM),
    pair("PYUSD-USDC", PYUSD, USDC),
    pair("yUSDC-USDC", YUSDC, USDC)
  ];

  function assetType(a) {
    if (!a || a.native || !a.issuer) return "native";
    return a.code.length > 4 ? "credit_alphanum12" : "credit_alphanum4";
  }

  function assetQuery(prefix, a) {
    if (!a || a.native || !a.issuer) return prefix + "_asset_type=native";
    return (
      prefix +
      "_asset_type=" +
      assetType(a) +
      "&" +
      prefix +
      "_asset_code=" +
      encodeURIComponent(a.code) +
      "&" +
      prefix +
      "_asset_issuer=" +
      encodeURIComponent(a.issuer)
    );
  }

  function orderBookUrl(p, limit) {
    var n = limit == null ? BOOK_LIMIT : limit;
    return HORIZON + "/order_book?" + assetQuery("selling", p.base) + "&" + assetQuery("buying", p.counter) + "&limit=" + n;
  }

  function tradesUrl(p, limit) {
    var n = limit == null ? 1 : limit;
    return HORIZON + "/trades?" + assetQuery("base", p.base) + "&" + assetQuery("counter", p.counter) + "&order=desc&limit=" + n;
  }

  function tradeAggregationsUrl(p, startMs, endMs, resolution) {
    var res = resolution || HOUR_MS;
    return (
      HORIZON +
      "/trade_aggregations?" +
      assetQuery("base", p.base) +
      "&" +
      assetQuery("counter", p.counter) +
      "&resolution=" +
      res +
      "&start_time=" +
      startMs +
      "&end_time=" +
      endMs +
      "&limit=24"
    );
  }

  function stellarChainAssetUrl(a) {
    if (!a || a.native || !a.issuer) return STELLARCHAIN_ASSET + "XLM";
    return STELLARCHAIN_ASSET + a.code + "-" + a.issuer;
  }

  function alignHour(ms) {
    return Math.floor(Number(ms) / HOUR_MS) * HOUR_MS;
  }

  function windowFor24h(nowMs) {
    var end = alignHour(nowMs);
    var start = end - DAY_MS;
    return { start: start, end: end, resolution: HOUR_MS };
  }

  function num(v) {
    var n = typeof v === "number" ? v : parseFloat(v);
    return isFinite(n) ? n : null;
  }

  function priceOfTrade(rec) {
    if (!rec) return null;
    if (rec.price && rec.price.n != null && rec.price.d) {
      var p = Number(rec.price.n) / Number(rec.price.d);
      return p > 0 ? p : null;
    }
    var b = num(rec.base_amount),
      c = num(rec.counter_amount);
    if (b > 0 && c > 0) return c / b;
    return null;
  }

  function parseLevels(rows) {
    var out = [];
    (rows || []).forEach(function (row) {
      var price = num(row && row.price);
      var amount = num(row && row.amount);
      if (price > 0 && amount > 0) out.push({ price: price, amount: amount });
    });
    return out;
  }

  function parseOrderBook(book) {
    var bids = parseLevels(book && book.bids);
    var asks = parseLevels(book && book.asks);
    var bid = bids[0] ? bids[0].price : null;
    var ask = asks[0] ? asks[0].price : null;
    var bidSize = bids[0] ? bids[0].amount : null;
    var askSize = asks[0] ? asks[0].amount : null;
    var mid = bid > 0 && ask > 0 ? (bid + ask) / 2 : null;
    var spread = bid > 0 && ask > 0 && mid > 0 ? ask - bid : null;
    var spreadPct = spread != null && mid > 0 ? (spread / mid) * 100 : null;
    return {
      bids: bids,
      asks: asks,
      bid: bid,
      ask: ask,
      bidSize: bidSize,
      askSize: askSize,
      mid: mid,
      spread: spread,
      spreadPct: spreadPct,
      hasBook: bids.length > 0 && asks.length > 0
    };
  }

  function parseTrades(payload) {
    var recs = payload && payload._embedded && payload._embedded.records;
    var rec = recs && recs[0];
    if (!rec) return { last: null, at: null };
    return { last: priceOfTrade(rec), at: rec.ledger_close_time || null };
  }

  function parseAggregations(payload) {
    var recs = (payload && payload._embedded && payload._embedded.records) || [];
    if (!recs.length) {
      return { open: null, close: null, changePct: null, volumeBase: null, volumeCounter: null, trades: null, candles: [] };
    }
    var candles = recs.map(function (r) {
      return {
        t: Number(r.timestamp),
        open: num(r.open),
        high: num(r.high),
        low: num(r.low),
        close: num(r.close),
        baseVolume: num(r.base_volume),
        counterVolume: num(r.counter_volume),
        tradeCount: num(r.trade_count)
      };
    });
    var open = candles[0].open;
    var close = candles[candles.length - 1].close;
    var volumeBase = 0,
      volumeCounter = 0,
      trades = 0,
      i;
    for (i = 0; i < candles.length; i++) {
      volumeBase += candles[i].baseVolume || 0;
      volumeCounter += candles[i].counterVolume || 0;
      trades += candles[i].tradeCount || 0;
    }
    var changePct = open > 0 && close > 0 ? ((close - open) / open) * 100 : null;
    return {
      open: open,
      close: close,
      changePct: changePct,
      volumeBase: volumeBase,
      volumeCounter: volumeCounter,
      trades: trades,
      candles: candles
    };
  }

  function unavailable(pair, reason) {
    return {
      id: pair.id,
      label: pair.label,
      base: pair.base,
      counter: pair.counter,
      ok: false,
      unavailable: true,
      reason: reason || "Live data unavailable",
      last: null,
      changePct: null,
      volumeBase: null,
      volumeCounter: null,
      spread: null,
      spreadPct: null,
      bid: null,
      ask: null,
      bidSize: null,
      askSize: null,
      mid: null,
      bids: [],
      asks: [],
      lastTradeAt: null,
      stellarChain: {
        base: stellarChainAssetUrl(pair.base),
        counter: stellarChainAssetUrl(pair.counter)
      },
      source: SOURCE_ID
    };
  }

  function summarizePair(pair, book, trades, aggs) {
    var parsedBook = parseOrderBook(book);
    var parsedTrades = parseTrades(trades);
    var parsedAggs = parseAggregations(aggs);
    var last = parsedTrades.last || parsedBook.mid || parsedAggs.close || null;
    var hasAny = last > 0 || parsedBook.hasBook;
    if (!hasAny) return unavailable(pair, "Live data unavailable");
    var changePct = null;
    if (parsedAggs.open > 0 && last > 0) changePct = ((last - parsedAggs.open) / parsedAggs.open) * 100;
    else changePct = parsedAggs.changePct;
    return {
      id: pair.id,
      label: pair.label,
      base: pair.base,
      counter: pair.counter,
      ok: true,
      unavailable: false,
      reason: null,
      last: last,
      changePct: changePct,
      volumeBase: parsedAggs.volumeBase,
      volumeCounter: parsedAggs.volumeCounter,
      spread: parsedBook.spread,
      spreadPct: parsedBook.spreadPct,
      bid: parsedBook.bid,
      ask: parsedBook.ask,
      bidSize: parsedBook.bidSize,
      askSize: parsedBook.askSize,
      mid: parsedBook.mid,
      bids: parsedBook.bids,
      asks: parsedBook.asks,
      lastTradeAt: parsedTrades.at,
      stellarChain: {
        base: stellarChainAssetUrl(pair.base),
        counter: stellarChainAssetUrl(pair.counter)
      },
      source: SOURCE_ID,
      candles: parsedAggs.candles
    };
  }

  function getJson(url, fetchImpl, signal) {
    return Promise.resolve(
      fetchImpl(url, { cache: "no-store", signal: signal, headers: { Accept: "application/json" } })
    ).then(function (r) {
      if (!r || !r.ok) {
        var status = r && r.status;
        var err = new Error(status ? "HTTP " + status : "network error");
        err.status = status;
        throw err;
      }
      return r.json();
    });
  }

  function settle(promise) {
    return promise.then(
      function (v) {
        return { ok: true, value: v };
      },
      function (e) {
        return { ok: false, error: e };
      }
    );
  }

  function fetchPair(pair, opts) {
    opts = opts || {};
    var fetchImpl = opts.fetch || fetch;
    var now = opts.now || Date.now();
    var signal = opts.signal;
    var win = windowFor24h(now);
    return Promise.all([
      settle(getJson(orderBookUrl(pair, opts.bookLimit || BOOK_LIMIT), fetchImpl, signal)),
      settle(getJson(tradesUrl(pair, 1), fetchImpl, signal)),
      settle(getJson(tradeAggregationsUrl(pair, win.start, win.end, win.resolution), fetchImpl, signal))
    ]).then(function (parts) {
      if (!parts[0].ok && !parts[1].ok && !parts[2].ok) {
        var why = (parts[0].error && parts[0].error.message) || "Live data unavailable";
        return unavailable(pair, "Live data unavailable (" + why + ")");
      }
      return summarizePair(
        pair,
        parts[0].ok ? parts[0].value : { bids: [], asks: [] },
        parts[1].ok ? parts[1].value : { _embedded: { records: [] } },
        parts[2].ok ? parts[2].value : { _embedded: { records: [] } }
      );
    });
  }

  function mapPool(items, limit, fn) {
    var i = 0;
    var out = new Array(items.length);
    function next() {
      if (i >= items.length) return Promise.resolve();
      var idx = i++;
      return Promise.resolve(fn(items[idx], idx)).then(function (v) {
        out[idx] = v;
        return next();
      });
    }
    var workers = [];
    var n = Math.min(limit, items.length);
    for (var w = 0; w < n; w++) workers.push(next());
    return Promise.all(workers).then(function () {
      return out;
    });
  }

  function fetchAll(opts) {
    opts = opts || {};
    var pairs = opts.pairs || PAIRS;
    var concurrency = opts.concurrency || 3;
    return mapPool(pairs, concurrency, function (p) {
      return fetchPair(p, opts);
    }).then(function (markets) {
      var ok = markets.filter(function (m) {
        return m && m.ok;
      }).length;
      return {
        ok: ok > 0,
        unavailable: ok === 0,
        source: SOURCE_ID,
        sourceLabel: SOURCE_LABEL,
        substitutionReason: SUBSTITUTION_REASON,
        fetchedAt: opts.now || Date.now(),
        refreshMs: REFRESH_MS,
        markets: markets
      };
    });
  }

  function findPair(id) {
    var want = String(id || "").toUpperCase().replace("/", "-");
    for (var i = 0; i < PAIRS.length; i++) {
      if (PAIRS[i].id.toUpperCase() === want || PAIRS[i].label.toUpperCase() === String(id || "").toUpperCase()) {
        return PAIRS[i];
      }
    }
    return null;
  }

  function fmtPx(v) {
    var n = num(v);
    if (n == null) return "—";
    if (n >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
    if (n >= 1) return n.toFixed(4);
    if (n >= 0.01) return n.toFixed(5);
    return n.toPrecision(4);
  }

  function fmtPct(v) {
    var n = num(v);
    if (n == null) return "—";
    var sign = n > 0 ? "+" : "";
    return sign + n.toFixed(2) + "%";
  }

  function fmtAmt(v) {
    var n = num(v);
    if (n == null) return "—";
    if (n >= 1000000) return (n / 1000000).toFixed(2) + "M";
    if (n >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (n >= 1) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
    return n.toPrecision(3);
  }

  function formatUpdated(ms, timeZone) {
    var tz = timeZone || "America/Phoenix";
    var d = new Date(ms);
    if (isNaN(d.getTime())) return "—";
    var text = d.toLocaleString("en-US", {
      timeZone: tz,
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true
    });
    return text + " " + tz;
  }

  return {
    HORIZON: HORIZON,
    REFRESH_MS: REFRESH_MS,
    SOURCE_ID: SOURCE_ID,
    SOURCE_LABEL: SOURCE_LABEL,
    SUBSTITUTION_REASON: SUBSTITUTION_REASON,
    PAIRS: PAIRS,
    ASSETS: { XLM: XLM, USDC: USDC, EURC: EURC, SHX: SHX, yXLM: YXLM, AQUA: AQUA, PYUSD: PYUSD, yUSDC: YUSDC },
    assetType: assetType,
    assetQuery: assetQuery,
    orderBookUrl: orderBookUrl,
    tradesUrl: tradesUrl,
    tradeAggregationsUrl: tradeAggregationsUrl,
    stellarChainAssetUrl: stellarChainAssetUrl,
    windowFor24h: windowFor24h,
    parseOrderBook: parseOrderBook,
    parseTrades: parseTrades,
    parseAggregations: parseAggregations,
    summarizePair: summarizePair,
    unavailable: unavailable,
    fetchPair: fetchPair,
    fetchAll: fetchAll,
    findPair: findPair,
    fmtPx: fmtPx,
    fmtPct: fmtPct,
    fmtAmt: fmtAmt,
    formatUpdated: formatUpdated
  };
});
