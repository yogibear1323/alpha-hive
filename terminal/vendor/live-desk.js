/* Alpha Hive Live session desk (local bridge only).
 * Shows XLM/USDC orders the bridge submitted on the Stellar DEX with the dedicated bot
 * account: open offers with Cancel, fills, transaction links, P&L and daily loss used,
 * plus Cancel all and the kill switch. Mirrors Queen LONG/SHORT calls on XLM rounds to
 * POST /stellar/orders. The bridge decides everything (risk, caps, arming, network):
 * this file holds no keys and never signs. It never uses the in-browser Alpha Hive key.
 */
(function () {
  "use strict";
  var POLL_MS = 15000;
  var timer = null;
  var last = null;
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") e.className = attrs[k]; else e.setAttribute(k, attrs[k]);
    });
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function auth() { return window.G6_AUTH || null; }
  function usable() { var a = auth(); return !!(a && !a.public && a.unlocked && a.unlocked()); }
  function liveMode() { return document.body.classList.contains("live-session"); }
  function call(path, opt) { return auth().call(path, opt || {}); }
  function money(n) { var v = Number(n); return isFinite(v) ? (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2) : "-"; }
  function num(n, d) { var v = Number(n); return isFinite(v) ? v.toFixed(d == null ? 4 : d) : "-"; }
  function safeLink(u) { return typeof u === "string" && /^https:\/\/stellar\.expert\/explorer\/(testnet|public)\/tx\/[0-9a-f]{64}$/.test(u) ? u : null; }
  function when(ts) { try { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); } catch (e) { return ""; } }
  function stat(msg, cls) { var s = $("deskStat"); if (s) { s.textContent = msg || ""; s.className = "stellar-stat " + (cls || ""); } }

  function css() {
    if ($("liveDeskCss")) return;
    var st = el("style", { id: "liveDeskCss" });
    st.textContent =
      ".live-desk{margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,140,66,.45)}" +
      ".live-desk h3{margin:0 0 6px;font:700 13px var(--mono);color:var(--text)}" +
      ".live-desk .desk-kpis{display:flex;flex-wrap:wrap;gap:6px 16px;font:12px var(--mono);color:var(--text);margin:6px 0}" +
      ".live-desk .desk-kpis b{color:#ffd84d;font-weight:700}" +
      ".live-desk .lossbar{height:8px;border-radius:4px;background:rgba(255,255,255,.12);overflow:hidden;margin:4px 0 8px}" +
      ".live-desk .lossbar span{display:block;height:100%;background:#ff8a80}" +
      ".live-desk table{width:100%;border-collapse:collapse;font:12px var(--mono);color:var(--text);margin:4px 0 10px}" +
      ".live-desk th,.live-desk td{text-align:left;padding:4px 6px;border-bottom:1px solid rgba(255,182,39,.18);white-space:nowrap}" +
      ".live-desk th{color:#ffd84d;font-weight:700}" +
      ".live-desk a{color:#8fd3ff}" +
      ".live-desk .tbl{overflow-x:auto}" +
      ".live-desk .kill{background:#b3261e;color:#fff;border-color:#ff8a80}" +
      ".live-desk .s-filled{color:#8fe388}.live-desk .s-failed{color:#ff8a80}.live-desk .s-open,.live-desk .s-partial{color:#ffd84d}";
    document.head.appendChild(st);
  }

  function build() {
    var box = $("liveArmBox");
    if (!box || $("liveDesk")) return;
    css();
    var d = el("div", { id: "liveDesk", class: "live-desk", "aria-live": "polite" });
    d.appendChild(el("h3", null, "Live orders · XLM/USDC on the Stellar DEX"));
    d.appendChild(el("p", { class: "stellar-note" },
      "Orders are signed by the dedicated bot account on the local bridge, never by this page or your Alpha Hive key. " +
      "Risk must approve every call; the $25 position cap, $5 daily loss stop and kill switch live in the bridge."));
    d.appendChild(el("div", { class: "desk-kpis", id: "deskKpis" }));
    var bar = el("div", { class: "lossbar", id: "deskLossBar", role: "img", "aria-label": "Daily loss used" });
    bar.appendChild(el("span", { id: "deskLossFill", style: "width:0%" }));
    d.appendChild(bar);
    var acts = el("div", { class: "stellar-convert-actions" });
    var r = el("button", { class: "btn ghost sm", id: "deskRefresh", type: "button" }, "Refresh");
    var ca = el("button", { class: "btn ghost sm", id: "deskCancelAll", type: "button" }, "Cancel all offers");
    var k = el("button", { class: "btn kill", id: "deskKill", type: "button" }, "Kill switch");
    acts.appendChild(r); acts.appendChild(ca); acts.appendChild(k);
    d.appendChild(acts);
    d.appendChild(el("p", { class: "stellar-stat", id: "deskStat", role: "status" }));
    d.appendChild(el("h3", null, "Open offers"));
    d.appendChild(el("div", { class: "tbl", id: "deskOpen" }));
    d.appendChild(el("h3", null, "Submitted orders"));
    d.appendChild(el("div", { class: "tbl", id: "deskOrders" }));
    d.appendChild(el("h3", null, "Fills"));
    d.appendChild(el("div", { class: "tbl", id: "deskFills" }));
    box.appendChild(d);
    r.onclick = function () { refresh(true); };
    ca.onclick = cancelAll;
    k.onclick = kill;
  }

  function table(host, head, rows) {
    host.textContent = "";
    if (!rows.length) { host.appendChild(el("p", { class: "stellar-note" }, "None yet.")); return; }
    var t = el("table"), th = el("tr");
    head.forEach(function (h) { th.appendChild(el("th", { scope: "col" }, h)); });
    var thead = el("thead"); thead.appendChild(th); t.appendChild(thead);
    var tb = el("tbody");
    rows.forEach(function (cells) {
      var tr = el("tr");
      cells.forEach(function (c) { var td = el("td"); if (c instanceof Node) td.appendChild(c); else td.textContent = c == null ? "" : String(c); tr.appendChild(td); });
      tb.appendChild(tr);
    });
    t.appendChild(tb); host.appendChild(t);
  }
  function txCell(url, hash) {
    var u = safeLink(url);
    if (!u) return hash ? String(hash).slice(0, 10) + "…" : "-";
    var a = el("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, String(hash).slice(0, 10) + "…");
    a.setAttribute("aria-label", "Transaction " + hash + " on stellar.expert");
    return a;
  }

  function render(s) {
    last = s;
    var k = $("deskKpis"); if (!k) return;
    k.textContent = "";
    var daily = s.daily || {}, pnl = s.pnl || {}, lim = s.limits || {};
    var used = Number(daily.loss_usd || 0), cap = Number(daily.limit_usd || lim.daily_loss_stop_usd || 5);
    function kv(label, val) { var sp = el("span"); sp.appendChild(document.createTextNode(label + " ")); sp.appendChild(el("b", null, val)); k.appendChild(sp); }
    kv("Mode", (s.mode || "PREVIEW") + " · " + (s.network || "testnet"));
    kv("Daily loss used", money(used) + " of " + money(cap) + (daily.stopped ? " · STOPPED for today" : ""));
    kv("Today P&L", money(pnl.today_pnl_usd));
    kv("Position", num(pnl.net_xlm, 2) + " XLM (" + money(pnl.position_usd) + " of " + money(lim.max_position_usd || 25) + ")");
    if (s.balances) kv("Bot balance", num(s.balances.usdc, 2) + " USDC · " + num(s.balances.xlm, 2) + " XLM");
    if (s.kill_switch) kv("Kill switch", "ON");
    var f = $("deskLossFill"); if (f) f.style.width = Math.min(100, cap > 0 ? used / cap * 100 : 0).toFixed(0) + "%";
    var bar = $("deskLossBar"); if (bar) bar.setAttribute("aria-label", "Daily loss used " + money(used) + " of " + money(cap));
    var orders = s.orders || [];
    var openRows = orders.filter(function (o) { return o.status === "open" || o.status === "partial"; }).map(function (o) {
      var b = el("button", { class: "btn ghost sm", type: "button" }, "Cancel");
      b.setAttribute("aria-label", "Cancel offer " + o.offer_id);
      b.onclick = function () { cancelOne(o.id, b); };
      return [o.offer_id, o.side, o.remaining || o.xlm, o.price, o.status, txCell(o.tx_link, o.tx_hash), b];
    });
    // Offers that exist on-chain but not in this bridge's ledger still show (cancel via Cancel all).
    var known = {}; orders.forEach(function (o) { if (o.offer_id) known[o.offer_id] = 1; });
    (s.open_offers || []).forEach(function (x) { if (!known[x.id]) openRows.push([x.id, "sell " + x.selling + " for " + x.buying, x.amount, x.price, "open (external)", "-", ""]); });
    table($("deskOpen"), ["Offer", "Side", "Amount", "Price", "Status", "Tx", ""], openRows);
    table($("deskOrders"), ["Time", "Side", "Type", "XLM", "Price", "Filled XLM", "Status", "Tx"], orders.slice(0, 20).map(function (o) {
      var st = el("span", { class: "s-" + o.status }, o.status + (o.error ? " · " + o.error : ""));
      return [when(o.ts), o.side, o.type, o.xlm, o.price, o.filled_xlm, st, txCell(o.tx_link, o.tx_hash)];
    }));
    table($("deskFills"), ["Time", "Side", "XLM", "USDC", "Price"], (s.fills || []).slice(0, 20).map(function (x) {
      return [when(x.ts), x.side, x.xlm, x.usdc, x.price];
    }));
    if (s.error) stat(s.error, "warn");
  }

  function refresh(manual) {
    build();
    if (!usable() || !$("liveDesk")) return Promise.resolve(null);
    if (manual) stat("Refreshing…");
    return call("/stellar/orders").then(function (s) { render(s); if (manual && !s.error) stat("Updated " + new Date().toLocaleTimeString(), "ok"); return s; })
      .catch(function (e) { stat(e.message || String(e), "err"); return null; });
  }
  function cancelOne(id, btn) {
    if (btn) btn.disabled = true;
    stat("Cancelling…");
    call("/stellar/orders/" + encodeURIComponent(id) + "/cancel", { method: "POST" })
      .then(function (r) { stat(r.ok ? "Offer cancelled." : "Cancel failed: " + (r.error || ""), r.ok ? "ok" : "err"); return refresh(); })
      .catch(function (e) { stat(e.message || String(e), "err"); if (btn) btn.disabled = false; });
  }
  function cancelAll() {
    stat("Cancelling all open offers…");
    call("/stellar/cancel-all", { method: "POST" })
      .then(function (r) { stat(r.ok ? "Cancelled " + (r.cancelled || []).length + " offer(s)." : "Cancel failed: " + (r.error || ""), r.ok ? "ok" : "err"); return refresh(); })
      .catch(function (e) { stat(e.message || String(e), "err"); });
  }
  function kill() {
    if (!window.confirm("Engage the kill switch? It cancels every open bot offer, disarms Live session and blocks new orders until you release it on this machine (python -m bridge release-kill).")) return;
    stat("Kill switch engaging…", "warn");
    call("/stellar/kill", { method: "POST", body: JSON.stringify({ reason: "terminal kill switch" }) })
      .then(function (r) {
        var n = r.cancel && r.cancel.cancelled ? r.cancel.cancelled.length : 0;
        stat("Kill switch ON · Live disarmed · " + n + " offer(s) cancelled. Release with python -m bridge release-kill.", "err");
        if ($("liveArmStat")) $("liveArmStat").textContent = "Kill switch ON. Live disarmed.";
        return refresh();
      })
      .catch(function (e) { stat(e.message || String(e), "err"); });
  }

  /* Called by the session loop for each round. Only XLM rounds in Live session mode go to the bridge. */
  function mirror(o) {
    if (!liveMode() || !usable()) return Promise.resolve(null);
    if (String(o.coin || "").toUpperCase() !== "XLM") { stat("Round " + o.r + ": " + o.coin + " stays in simulation. Live session sends XLM/USDC only.", ""); return Promise.resolve(null); }
    if (o.risk !== "approve") return Promise.resolve(null);   // checked again, independently, by the bridge
    var body = { symbol: "XLM-USDC", side: o.dir > 0 ? "buy" : "sell", stop: o.stop, target: o.target,
      entry: o.entry, entry_ref: o.entry, risk_pct: o.pct, balance: o.balance, risk: { status: o.risk },
      queen: { call: o.dir > 0 ? "LONG" : "SHORT" }, client_ref: "live-round-" + o.r, order_type: "market", slippage_pct: 0.5 };
    return call("/stellar/orders", { method: "POST", body: JSON.stringify(body) }).then(function (j) {
      if (j.preview) stat("Round " + o.r + ": preview only (" + (j.why || "not armed") + "). No order sent.", "warn");
      else if (j.order) stat("Round " + o.r + ": " + j.order.side + " " + j.order.xlm + " XLM · " + j.order.status + (j.order.error ? " · " + j.order.error : ""), j.order.status === "failed" ? "err" : "ok");
      refresh();
      return j;
    }).catch(function (e) { stat("Round " + o.r + ": no order · " + (e.message || String(e)), "warn"); return null; });
  }

  function tick() { if (liveMode() && usable()) refresh(false); }
  function init() {
    build();
    if (timer) clearInterval(timer);
    timer = setInterval(tick, POLL_MS);
    setTimeout(tick, 800);
  }
  window.AlphaHiveLiveDesk = { init: init, refresh: refresh, mirror: mirror, last: function () { return last; } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
