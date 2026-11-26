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

