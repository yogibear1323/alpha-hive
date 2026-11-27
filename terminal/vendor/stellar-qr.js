/**
 * Client-side Stellar deposit QR codes (no CDN, no network).
 * Uses the vendored MIT qrcode-generator library.
 */
(function (root, factory) {
  var api = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AlphaHiveQr = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  var ADDR_RE = /^G[A-Z2-7]{55}$/;

  function loadLib() {
    if (root && typeof root.qrcode === "function") return root.qrcode;
    try { return require("./qrcode-generator.js"); } catch (e) {}
    return null;
  }

  function isValidAddress(addr) {
    return typeof addr === "string" && ADDR_RE.test(addr.trim());
  }

  function svgFor(address) {
    if (!isValidAddress(address)) return "";
    var qrcode = loadLib();
    if (typeof qrcode !== "function") return "";
    var qr = qrcode(0, "M");
    qr.addData(address.trim());
    qr.make();
    var alt = { text: "QR code for Stellar address " + address.trim() };
    return qr.createSvgTag(4, 2, alt);
  }

  function render(el, address) {
    if (!el) return false;
    var svg = svgFor(address);
    if (!svg) {
      el.innerHTML = "";
      el.hidden = true;
      return false;
    }
    el.innerHTML = svg;
    el.hidden = false;
    var svgEl = el.querySelector && el.querySelector("svg");
    if (svgEl) {
      svgEl.setAttribute("role", "img");
      svgEl.setAttribute("aria-label", "QR code for Stellar address " + address.trim());
    }
    return true;
  }

  return { isValidAddress: isValidAddress, svgFor: svgFor, render: render };
});
