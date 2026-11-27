/**
 * Unit tests for the Stellar DEX market adapter.
 * Run: node --test tests/test_stellar_dex_markets.js
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const m = require(path.join(__dirname, "..", "terminal", "vendor", "stellar-dex-markets.js"));

const USDC = "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN";
const EURC = "GDHU6WRG4IEQXM5NZ4BMPKOXHW76MZM4Y2IEMFDVXBSDP6SJY4ITNPP2";
const SHX = "GDSTRSHXHGJ7ZIVRBXEYE5Q74XUVCUSEKEBR7UCHEUUEK72N7I7KJ6JH";
const YXLM = "GARDNV3Q7YGT4AKSDF25LT32YSCCW4EV22Y2TV3I2PU2MMXJTEDL5T55";
const AQUA = "GBNZILSTVQZ4R7IKQDGHYGY2QXL5QOFJYQMXPKWRRM5PAV7Y4M67AQUA";
const PYUSD = "GDQE7IXJ4HUHV6RQHIUPRJSEZE4DRS5WY577O2FY6YQ5LVWZ7JZTU2V5";
const YUSDC = "GDGTVWSM4MGS4T7Z6W4RPWOCHE2I6RDFCIFZGS3DOA63LWQTRNZNTTFF";
const MYKOBO = "GAQRF3UGHBT6JYQZ7YSUYCIYWAF4T2SAA5237Q5LIQYJOHHFAWDXZ7NM";

describe("pair catalog", () => {
  it("lists the eight ranked official pairs with known issuers", () => {
    assert.equal(m.PAIRS.length, 8);
    assert.deepEqual(
      m.PAIRS.map((p) => p.id),
      ["XLM-USDC", "EURC-USDC", "XLM-EURC", "SHX-XLM", "yXLM-XLM", "AQUA-XLM", "PYUSD-USDC", "yUSDC-USDC"]
    );
    const byId = Object.fromEntries(m.PAIRS.map((p) => [p.id, p]));
    assert.equal(byId["XLM-USDC"].base.native, true);
    assert.equal(byId["XLM-USDC"].counter.issuer, USDC);
    assert.equal(byId["EURC-USDC"].base.issuer, EURC);
    assert.equal(byId["EURC-USDC"].counter.issuer, USDC);
    assert.equal(byId["XLM-EURC"].counter.issuer, EURC);
    assert.equal(byId["SHX-XLM"].base.issuer, SHX);
    assert.equal(byId["yXLM-XLM"].base.issuer, YXLM);
    assert.equal(byId["AQUA-XLM"].base.issuer, AQUA);
    assert.equal(byId["PYUSD-USDC"].base.issuer, PYUSD);
    assert.equal(byId["yUSDC-USDC"].base.issuer, YUSDC);
  });

  it("does not include the Mykobo EURC issuer", () => {
    const issuers = JSON.stringify(m.PAIRS);
    assert.equal(issuers.includes(MYKOBO), false);
  });

  it("finds a pair by id or slash label", () => {
    assert.equal(m.findPair("xlm-usdc").label, "XLM/USDC");
    assert.equal(m.findPair("XLM/USDC").id, "XLM-USDC");
    assert.equal(m.findPair("nope"), null);
  });
});

describe("Horizon URL builders", () => {
  it("builds order book, trades, and aggregation URLs for native and credit assets", () => {
    const p = m.findPair("XLM-USDC");
    const book = m.orderBookUrl(p, 10);
    assert.match(book, /^https:\/\/horizon\.stellar\.org\/order_book\?/);
    assert.match(book, /selling_asset_type=native/);
    assert.match(book, /buying_asset_type=credit_alphanum4/);
    assert.match(book, new RegExp("buying_asset_code=USDC"));
    assert.match(book, new RegExp("buying_asset_issuer=" + USDC));
    const tr = m.tradesUrl(p, 1);
    assert.match(tr, /\/trades\?/);
    assert.match(tr, /base_asset_type=native/);
    assert.match(tr, /counter_asset_code=USDC/);
    const ag = m.tradeAggregationsUrl(p, 1000, 2000, 3600000);
    assert.match(ag, /\/trade_aggregations\?/);
    assert.match(ag, /resolution=3600000/);
    assert.match(ag, /start_time=1000/);
    assert.match(ag, /end_time=2000/);
  });

  it("uses alphanum12 for PYUSD", () => {
    const p = m.findPair("PYUSD-USDC");
    assert.equal(m.assetType(p.base), "credit_alphanum12");
    assert.match(m.orderBookUrl(p), /selling_asset_type=credit_alphanum12/);
    assert.match(m.orderBookUrl(p), /selling_asset_code=PYUSD/);
  });

  it("builds Stellarchain asset links", () => {
    assert.equal(m.stellarChainAssetUrl(m.ASSETS.XLM), "https://stellarchain.io/assets/XLM");
    assert.equal(m.stellarChainAssetUrl(m.ASSETS.USDC), "https://stellarchain.io/assets/USDC-" + USDC);
  });
});

describe("parsers", () => {
  it("computes mid, spread, and top-of-book depth", () => {
    const book = m.parseOrderBook({
      bids: [
        { price: "0.12695", amount: "10" },
        { price: "0.12690", amount: "20" },
      ],
      asks: [
        { price: "0.12705", amount: "8" },
        { price: "0.12710", amount: "15" },
      ],
    });
    assert.equal(book.hasBook, true);
    assert.equal(book.bid, 0.12695);
    assert.equal(book.ask, 0.12705);
    assert.ok(Math.abs(book.mid - 0.127) < 1e-12);
    assert.ok(Math.abs(book.spread - 0.0001) < 1e-12);
    assert.ok(book.spreadPct > 0);
    assert.equal(book.bidSize, 10);
    assert.equal(book.askSize, 8);
    assert.equal(book.bids.length, 2);
  });

  it("treats an empty book as having no quote", () => {
    const book = m.parseOrderBook({ bids: [], asks: [] });
    assert.equal(book.hasBook, false);
    assert.equal(book.mid, null);
    assert.equal(book.spread, null);
  });

  it("reads last trade price from n/d", () => {
    const t = m.parseTrades({
      _embedded: {
        records: [
          {
            ledger_close_time: "2026-11-27T19:53:43Z",
            price: { n: "317807", d: "2500000" },
            base_amount: "1",
            counter_amount: "0.1",
          },
        ],
      },
    });
    assert.ok(Math.abs(t.last - 317807 / 2500000) < 1e-12);
    assert.equal(t.at, "2026-11-27T19:53:43Z");
  });

  it("sums 24h volume and change from aggregations", () => {
    const ag = m.parseAggregations({
      _embedded: {
        records: [
          { timestamp: "1", open: "1.00", close: "1.02", base_volume: "10", counter_volume: "10", trade_count: "2", high: "1.03", low: "0.99" },
          { timestamp: "2", open: "1.02", close: "1.10", base_volume: "5", counter_volume: "5.5", trade_count: "1", high: "1.11", low: "1.01" },
        ],
      },
    });
    assert.equal(ag.open, 1);
    assert.equal(ag.close, 1.1);
    assert.ok(Math.abs(ag.changePct - 10) < 1e-9);
    assert.equal(ag.volumeBase, 15);
    assert.equal(ag.volumeCounter, 15.5);
    assert.equal(ag.trades, 3);
    assert.equal(ag.candles.length, 2);
  });
});

describe("summarizePair", () => {
  const p = () => m.findPair("XLM-USDC");

  it("combines book, trade, and aggregations without inventing fields", () => {
    const s = m.summarizePair(
      p(),
      { bids: [{ price: "0.12", amount: "3" }], asks: [{ price: "0.13", amount: "4" }] },
      { _embedded: { records: [{ price: { n: "1", d: "8" }, ledger_close_time: "2026-11-27T00:00:00Z" }] } },
      { _embedded: { records: [{ open: "0.10", close: "0.12", base_volume: "100", counter_volume: "12", trade_count: "9", timestamp: "1", high: "0.13", low: "0.10" }] } }
    );
    assert.equal(s.ok, true);
    assert.equal(s.unavailable, false);
    assert.equal(s.last, 0.125);
    assert.ok(Math.abs(s.changePct - 25) < 1e-9);
    assert.equal(s.volumeCounter, 12);
    assert.equal(s.bid, 0.12);
    assert.equal(s.ask, 0.13);
    assert.equal(s.stellarChain.counter, "https://stellarchain.io/assets/USDC-" + USDC);
    assert.equal(s.source, "horizon");
  });

  it("returns a clear unavailable state when every payload is empty", () => {
    const s = m.summarizePair(p(), { bids: [], asks: [] }, { _embedded: { records: [] } }, { _embedded: { records: [] } });
    assert.equal(s.ok, false);
    assert.equal(s.unavailable, true);
    assert.equal(s.last, null);
    assert.match(s.reason, /unavailable/i);
  });
});

function jsonResp(body, status) {
  return {
    ok: status == null || status < 400,
    status: status || 200,
    json: async () => body,
  };
}

describe("fetchPair / fetchAll", () => {
  it("uses Horizon URLs and no API key header", async () => {
    const seen = [];
    const p = m.findPair("XLM-USDC");
    const fetchImpl = async (url, init) => {
      seen.push({ url, init });
      if (url.includes("/order_book")) {
        return jsonResp({ bids: [{ price: "0.12", amount: "1" }], asks: [{ price: "0.13", amount: "1" }] });
      }
      if (url.includes("/trades?")) {
        return jsonResp({ _embedded: { records: [{ price: { n: "12", d: "100" }, ledger_close_time: "2026-11-27T19:00:00Z" }] } });
      }
      if (url.includes("/trade_aggregations")) {
        return jsonResp({ _embedded: { records: [{ open: "0.11", close: "0.12", base_volume: "10", counter_volume: "1.2", trade_count: "3", timestamp: "1", high: "0.13", low: "0.11" }] } });
      }
      throw new Error("unexpected " + url);
    };
    const row = await m.fetchPair(p, { fetch: fetchImpl, now: 1_795_806_000_000 });
    assert.equal(row.ok, true);
    assert.equal(row.last, 0.12);
    assert.equal(seen.length, 3);
    for (const s of seen) {
      assert.match(s.url, /^https:\/\/horizon\.stellar\.org\//);
      assert.equal(s.init.cache, "no-store");
      assert.ok(!s.init.headers || !s.init.headers.Authorization);
      assert.ok(!String(JSON.stringify(s.init)).includes("Bearer"));
    }
  });

  it("marks a pair unavailable when every request fails, without inventing a price", async () => {
    const fetchImpl = async () => jsonResp({ error: "nope" }, 503);
    const row = await m.fetchPair(m.findPair("AQUA-XLM"), { fetch: fetchImpl, now: Date.now() });
    assert.equal(row.ok, false);
    assert.equal(row.last, null);
    assert.equal(row.unavailable, true);
    assert.match(row.reason, /unavailable/i);
  });

  it("fetchAll reports a global unavailable state when every pair fails", async () => {
    const snap = await m.fetchAll({
      fetch: async () => {
        throw new Error("network down");
      },
      now: 1_795_806_000_000,
      concurrency: 8,
    });
    assert.equal(snap.ok, false);
    assert.equal(snap.unavailable, true);
    assert.equal(snap.source, "horizon");
    assert.match(snap.sourceLabel, /Horizon/);
    assert.match(snap.substitutionReason, /StellarChain/);
    assert.equal(snap.markets.length, 8);
    assert.ok(snap.markets.every((row) => row.unavailable && row.last == null));
  });

  it("keeps successful pairs when others fail", async () => {
    const fetchImpl = async (url) => {
      if (url.includes("AQUA")) return jsonResp({}, 500);
      if (url.includes("/order_book")) return jsonResp({ bids: [{ price: "1", amount: "1" }], asks: [{ price: "1.1", amount: "1" }] });
      if (url.includes("/trades?")) return jsonResp({ _embedded: { records: [{ price: { n: "1", d: "1" } }] } });
      return jsonResp({ _embedded: { records: [] } });
    };
    const snap = await m.fetchAll({ fetch: fetchImpl, now: 1_795_806_000_000, concurrency: 4 });
    assert.equal(snap.ok, true);
    const aqua = snap.markets.find((x) => x.id === "AQUA-XLM");
    const xlm = snap.markets.find((x) => x.id === "XLM-USDC");
    assert.equal(aqua.unavailable, true);
    assert.equal(xlm.ok, true);
    assert.equal(xlm.last, 1);
  });
});

describe("formatters", () => {
  it("formats prices, percents, amounts, and Phoenix timestamps", () => {
    assert.equal(m.fmtPx(null), "—");
    assert.equal(m.fmtPx(0.12705), "0.12705");
    assert.equal(m.fmtPct(1.25), "+1.25%");
    assert.equal(m.fmtPct(-2), "-2.00%");
    assert.equal(m.fmtAmt(null), "—");
    assert.match(m.formatUpdated(Date.UTC(2026, 10, 27, 19, 54, 33), "America/Phoenix"), /America\/Phoenix/);
    assert.match(m.formatUpdated(Date.UTC(2026, 10, 27, 19, 54, 33), "America/Phoenix"), /12:54:33 PM/);
  });

  it("uses a 20 second refresh interval", () => {
    assert.equal(m.REFRESH_MS, 20000);
  });
});
