#!/bin/sh
# Rebuild the vendored Stellar Wallets Kit + Stellar SDK bundle.
# Usage (from repo root or this folder): sh tools/stellar-kit/build.sh
set -e
cd "$(dirname "$0")"
if [ ! -d node_modules/@creit.tech/stellar-wallets-kit ]; then
  npm install
fi
mkdir -p ../../terminal/vendor
npx esbuild entry.js \
  --bundle \
  --format=iife \
  --platform=browser \
  --target=es2020 \
  --legal-comments=none \
  --minify \
  --banner:js='/* Alpha Hive vendored @creit.tech/stellar-wallets-kit + @stellar/stellar-sdk. Rebuild: sh tools/stellar-kit/build.sh. No CDNs. */' \
  --outfile=../../terminal/vendor/stellar-wallets-kit.js
echo "Wrote terminal/vendor/stellar-wallets-kit.js ($(wc -c < ../../terminal/vendor/stellar-wallets-kit.js) bytes)"
