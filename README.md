# Alpha Hive: One Queen. Five bots. One hive. (AI trading simulation)

**Live site:** https://yogibear1323.github.io/alpha-hive/ · **Terminal:** https://yogibear1323.github.io/alpha-hive/terminal/

> Educational simulation on free public prices (Coinbase Exchange, Kraken fallback) with a virtual
> balance. Fictional EXMP/EXMQ packets remain only as a labelled "Practice (fictional)" option. It places no real orders, connects to no trading
> account, and is not financial advice.

`index.html` is the landing page, which explains the project, and `terminal/index.html` is the terminal itself. Each is a single, self-contained HTML file (no CDNs); the homepage videos and posters live in `assets/video/`. Add `?session`, `?fast` or `?offline` to the terminal address. Pick a
live coin (or a Practice packet), press **Run the hive**, and watch the hive work through it. The ♛ **Queen** (formerly Decision) is the boss: she tasks Scout, dispatches
News, Sentiment and Charts, sends the proposal to the Risk gate and makes the final call of `BUY — SIMULATION ONLY` (▲ long), `SELL/SHORT — SIMULATION ONLY` (▼ short), `WAIT`, or `SKIP`. A human
must review every result.

**Hierarchy and the guard.** Queen → Scout → workers (News · Sentiment · Charts) → 🛡 Risk → Queen's call. **Risk is the
hive's guard and answers to no one:** the Queen cannot override or bypass a Risk reject or reduce (a reject means she makes
no call; a reduce turns a long or short into WAIT). The Python project enforces this in `agents/queen.py` and refuses any
config that sets `hierarchy.queen_can_overrule_guard`.

**Videos:** the homepage shows a 37 s narrated sizzle (vertical cut on phones) and an 89 s narrated "How the hive trades"
explainer, both labelled *Simulation · demo funds* and recorded on the Queen design (crowned Queen bee, central gold chamber,
workers reporting back, Risk as the guard). The sizzle autoplays muted on loop (not under reduced motion); press **Sound on**
or use the controls to hear it. Narration is neural TTS; the music and sound effects are synthesized, with no samples.

- **Live coins (default):** single runs fetch real hourly and one-minute candles for the selected coin.
- **Ticker tape:** live USD prices and 24h change for your token list, refreshed every 10 s, marked LIVE;
  it says "Offline" instead of showing fake tickers if both feeds fail.
- **Status labels:** a header chip, the Queen's call, the position panel and the round tape show
  `LONG ▲ BUY` (green) or `SHORT ▼ SELL` (red), or WAIT/SKIP, reset every round; each analyst shows its own
  lean (bullish/bearish/neutral). "simulation only" is a separate tag.
- **Hive scene:** a hive band at the top of the terminal (first screen on phone and desktop) with a
  honeycomb dome, glowing entrance and six labelled chambers, with the ♛ Queen's larger, crowned royal chamber at the
  centre and the Risk guard set apart; honeycomb comb walls frame the page. Each worker's striped, winged bee
  flies out to its panel when the Queen dispatches it and reports back to her before returning home (Risk's
  bee goes straight home: it answers to no one), with a
  minimum out-and-back so it always reaches the panel. Scout's chamber glows when it picks a token, Risk's
  fills on approvals; when a trade closes, a win fills honey in the Queen's chamber and in each analyst that
  agreed, and a loss dims those cells. Honey persists across reloads (localStorage); Reset clears it.
  Canvas + requestAnimationFrame, paused when the tab is hidden, a still picture in reduced motion.
  Purely decorative: it changes nothing about prices, risk or trades.
- **Trade timer:** every open trade shows `TIME LEFT m:ss` with a draining ring and bar (amber under 15 s,
  red under 5 s). It freezes with the reason when a trade closes early (for example `STOP HIT at 0:31`).
- **Practice (fictional) EXMP sample:** Risk reduces the simulated size to $100, and the call is WAIT.
- **Practice (fictional) EXMQ missing price:** Risk rejects, the Queen makes no call, and the call is SKIP.
- **Demo session:** a virtual balance (default $10,000) trades the tokens you pick.
  - Prices are **live public prices**: Coinbase Exchange first, then Kraken. No keys, no sign-in, no
    real orders.
  - Each round, the Queen and all five bots analyse real candles for the next token. A BUY opens a virtual long and
    a SELL/SHORT a virtual short; each closes at the stop, the target, or the last live price when the window ends.
  - It runs continuously until you press **Stop**. Stop freezes the figures; **Resume** continues from
    the frozen balance.
  - If the feeds are unreachable, it switches to fictional paths labelled "Offline: simulated prices".
- **Tokens:** BTC, ETH and SOL are selected by default, with XRP, XLM, DOGE, ADA, AVAX and LINK ready
  to tap.
  - **Add token** accepts any symbol with a USD market on Coinbase Exchange or Kraken and rejects
    unknown ones.
  - **Add all majors** adds 20 large coins: CoinGecko's top 30 without stablecoins or pegged tokens,
    each re-checked for a USD price when you tap.
- **Per-bot risk:** 1% or 2%, or a custom 0.1–10%, of the **current** virtual balance.
  - The trade risks the average of the active bots, capped by the Risk bot's % (the ceiling).
  - Size = risk $ ÷ stop distance, capped at balance × leverage, so a stop-out loses about the risk $.
  - With a zero or unavailable balance, trading is disabled.
- **Virtual leverage · simulation only:** 1x, 2x, 5x, 10x, 20x (default), 50x or 100x, saved in your
  browser. Longs and shorts use the same risk sizing and cap; the panel and ledger show direction,
  leverage, notional and margin used. A virtual margin call closes a trade at the balance limit and
  stops trading. 1x matches the old unleveraged behaviour. No borrowing, orders or real money.
- **Long and short:** shorts put the stop above entry and the target below; P&L = size × (1 − exit ÷ entry).
  The summary shows the win rate for each direction.
- **Bot switches:** pause any bot from its panel.
  - A paused analyst counts as neutral.
  - A paused Risk gate turns every call into SKIP ("Risk paused").
  - A paused Queen makes no new trades.
- Virtual balance · live public prices · no real orders · not financial advice.

The page uses the Alpha Hive look: amber and honey on charcoal, a honeycomb of hexagonal bot panels, and
worker-bee particles that fly along shared flight paths and report back to the Queen; the proposal swarms into the Risk gate. Scout
runs a hex-pulse scan, honey drips down the background, and the final call arrives with a countdown, an amber glitch and a hexagon burst.
The **Motion** button in the header switches to reduced motion, and the page starts that way when your
device asks for less motion.

The rest of this repository is the full Python project the page mirrors: the Queen and five bots, schema checks with one
retry, a Risk gate enforced in code, an offline mock, an optional Grok (xAI) provider, a Flask browser
UI, and 12 safety tests. See [PROJECT_README.md](PROJECT_README.md) for setup on Mac, Linux, and Windows:

```
cp .env.example .env
pip install -r requirements.txt
python run_demo.py        # terminal run
python -m pytest -q       # 12 safety tests
python web/app.py         # browser UI at http://127.0.0.1:8642
```

## Broker bridge (Crucial Markets · TradeLocker) — demo first

The public site is **simulation only**. For people running Alpha Hive on their own computer, `bridge/` adds a
small local FastAPI service that can mirror Risk-approved LONG/SHORT calls to a **Crucial Markets TradeLocker
demo account**, with the round's stop and target as one SL and one TP, inside hard limits enforced by the bridge
(risk per trade, position size, 1:30 leverage cap, daily loss stop, max open positions, kill switch). Risk stays
the guard: a reject or reduce never reaches the broker. LIVE is off by default and needs an env flag, a restart
and a confirm in the terminal. Credentials stay in `bridge/.env` on your machine (git-ignored), never in the
browser, this repo or GitHub Pages. In the terminal, **Broker: Crucial demo** only works when the page and the
bridge both run on `localhost`.

Caveats: Crucial Markets is **unregulated** (South Africa / Saint Lucia), withdrawals are **crypto only**, and
leveraged CFDs can lose money fast. Test on demo before any real money. Not financial advice. Setup steps are in
`PROJECT_README.md`, section 9.

## Broker bridge (Coinbase Advanced Trade) — preview first

Coinbase is a **regulated US** alternative to Crucial. It has **no demo account**, so the bridge defaults to
**PREVIEW-ONLY**: it calls Coinbase's `preview_order` endpoint and **never** `create_order` until you set
`ALPHA_HIVE_ALLOW_LIVE=1` and tick **Arm LIVE** in a locally running terminal. Spot is **long-only** (SHORT
calls are refused; no margin or perps by default). Conservative default caps are a **$25** max position and a
**$5** daily loss stop. Keys (`COINBASE_API_KEY_NAME`, `COINBASE_API_PRIVATE_KEY`) live in `bridge/.env`
(git-ignored). Create a **trade-only** CDP secret API key with Transfer off and an IP allowlist — steps are in
`PROJECT_README.md`, section 10, and in `bridge/.env.example`. In the terminal, **Broker: Coinbase (live)** only
works when the page and the bridge both run on `localhost`.


## Stellar wallet connect

In the terminal's Demo session view (or add `?session` to the terminal address), you can connect a Stellar wallet (Varial, formerly Freighter; xBull; LOBSTR; Albedo; Hana; and others)
through a locally vendored copy of [Stellar Wallets Kit](https://stellarwalletskit.dev/). This is read-only by
default: it shows the public address and balances (XLM, USDC, and other assets) from Horizon, plus an approximate
USD value using the existing XLM-USD price feed. A **Look up** field can preview any public address (try the SDF
account) without connecting. Optional **Send** lets the connected wallet sign a payment; Alpha Hive never sees or
stores a secret key, and there is no secret-key input. Public network is the default, with a testnet toggle.
Wallet balances do not change the virtual trading balance, though you can copy the USD value into the starting
simulation balance. Rebuild the vendor bundle with `sh tools/stellar-kit/build.sh` if you update the kit.

### Convert XLM to USDC (Stellar DEX)

With a wallet connected, **Convert XLM to USDC** quotes a swap on the Stellar DEX (Horizon
`/paths/strict-send`, XLM → official Circle USDC) and shows the amount in, estimated USDC out, the route,
a slippage setting (default 0.5%) and the min received, plus a 0.5 XLM reserve note. If the account has no
USDC trustline, **Add USDC trustline** is a separate step the wallet signs first. The convert itself is a
`pathPaymentStrictSend` to your own address, signed in your wallet and submitted to Horizon. Alpha Hive never
asks for a secret key. The testnet toggle uses Circle's testnet USDC issuer and labels those tokens as test
(they have no cash value). After a successful convert, the USDC balance is shown as **Live session balance
(Stellar DEX)**. Live session trades run on the Stellar DEX only, for the XLM/USDC pair, through the
local bridge (no Coinbase transfer). Unit tests cover the quote and min-received math
(`node --test tests/test_stellar_wallet.js`). A testnet end-to-end script (`node --test tests/test_stellar_convert_e2e.js`)
uses Friendbot and a throwaway testnet keypair in that script only; it never submits on mainnet and is not
used by the page.

### Alpha Hive account (optional in-memory key)

The homepage **Create account / Sign in** button opens the official terminal sign-in panel
(`terminal/?signin=1`) *before* any key is entered, so a key never crosses a page load.
Create account asks for a public G address and the key and checks that they match; later
sign-in uses the key only and derives the address. The key is held in JavaScript memory
only, never in `localStorage`, `sessionStorage`, cookies, IndexedDB, the URL, or
`postMessage`. It is wiped on **Log out**, tab close, and after 15 minutes idle. Send and
Convert can be signed with that in-memory key after an explicit confirm dialog. Wallet
connect stays available as the alternative.

**Use a separate trading account with only what you are willing to risk, never your main
wallet.** Only enter a key at `yogibear1323.github.io/alpha-hive`. If you lose the key, it
cannot be recovered. The key session defaults to **testnet**; flipping to Public shows a
warning. Automated Live session order submission is **not** wired to this key — Live
real-money trading stays gated on the local bridge as before.

### Local unlock, Fund panel, and Live session

The public GitHub Pages site stays **simulation only**. Run the terminal and the bridge on your computer
to unlock a session, fund a dedicated bot account, or arm Live session.

1. Copy env files if you have not already: `cp .env.example .env` and `cp bridge/.env.example bridge/.env`.
   Put a long `BRIDGE_TOKEN` in `bridge/.env` (for CLI and tests; the page no longer pastes this token).
   Leave `STELLAR_NETWORK=testnet` and `ALPHA_HIVE_ALLOW_LIVE=0` unless you intend to arm mainnet later.
2. Install dependencies: `pip install -r requirements.txt -r bridge/requirements.txt`.
3. Set an unlock passphrase (hash only is saved, never printed):

       python -m bridge set-passphrase

   A wallet-signed challenge can issue the **same** short-lived session, alongside or instead of a
   passphrase (`POST /unlock/challenge` then `POST /unlock/wallet`). The page hook is in place; a full
   SEP-10 kit signature will plug into **Unlock with wallet**.
4. Optional dedicated bot account (secret stays in `bridge/.env` only — back that file up offline or
   bot funds are lost; never use your main wallet's secret):

       python -m bridge create-bot-account --allowlist GYOURWALLET...

   Withdrawals from that account are allowlisted to your wallet only.
5. In two terminals:

       python -m bridge
       TRADING_MODE=SIMULATION_ONLY python web/app.py

   Open http://127.0.0.1:8642, click **Unlock**, then **Live session**. The header shows a lock badge
   and **Log out**. Send, Convert, broker connect and Live arm stay locked until the session is open.
   Connecting a wallet is allowed while locked so a wallet can sign an unlock challenge later.
   Logout or 5 minutes of idle disarms Live and disconnects the wallet.
6. **Fund your account** (Live session, when a bot account exists) shows the public G address, a locally
   generated QR, and a Horizon deposit watch. A new account is not on-chain until it has at least 1 XLM
   (2+ XLM plus 0.5 XLM for a USDC trustline is recommended). No memo is needed. Then **Swap to USDC**.
   For your own wallet (no bot account), use **Convert XLM to USDC**; your wallet signs.
7. Live session is visually distinct (red/amber, LIVE badge). Trades are XLM/USDC only. The Risk guard,
   kill switch (`python -m bridge release-kill` is the only release) and a **$5** daily loss stop stay
   in the bridge and cannot be overruled. Position cap starts at **$25**. Testnet can be armed while
   unlocked. Mainnet also needs `ALPHA_HIVE_ALLOW_LIVE=1`, a restart, an unlocked session, and a typed
   confirm: `I UNDERSTAND THIS IS REAL MONEY` (not a JS `confirm()` click). Default is preview/testnet;
   this repo does not submit mainnet orders in tests.

StellarTerm.com is listed next to the wallet examples as a **non-clickable** note: it is a web exchange,
not a Wallets Kit connector, and it does not connect to Alpha Hive. The official site is
https://stellarterm.com only.


