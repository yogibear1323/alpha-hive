/**
 * Unit tests for the vendored deposit QR helper.
 * Run: node --test tests/test_stellar_qr.js
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const qr = require(path.join(__dirname, "..", "terminal", "vendor", "stellar-qr.js"));

const ADDR = "GDIO7AVQGQPQJRYGHKL36BZTVUK3BVWZK75HFLRWBACGLVF22EDES7SM";

describe("AlphaHiveQr", () => {
  it("rejects secrets and junk and accepts a public G address", () => {
    assert.equal(qr.isValidAddress(ADDR), true);
    assert.equal(qr.isValidAddress("S" + ADDR.slice(1)), false);
    assert.equal(qr.isValidAddress(""), false);
    assert.equal(qr.svgFor("S" + ADDR.slice(1)), "");
  });

  it("renders an SVG QR that includes the address as accessible text", () => {
    const svg = qr.svgFor(ADDR);
    assert.match(svg, /^<svg\b/i);
    assert.match(svg, /<rect\b|<path\b/i);
    assert.match(svg, /QR code for Stellar address /);
    assert.ok(svg.includes(ADDR), "SVG accessible text should include the address");
    assert.doesNotMatch(svg, /S[A-Z2-7]{55}/);
  });
});
