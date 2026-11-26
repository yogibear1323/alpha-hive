# Alpha Hive: Six-Bot AI Trading Team (simulation)

**Live site:** https://yogibear1323.github.io/alpha-hive/ · **Terminal:** https://yogibear1323.github.io/alpha-hive/terminal/

> Educational simulation. Single runs use fictional example data; the demo session reads free public
> prices. It places no real orders, connects to no trading
> account, and is not financial advice.

`index.html` is the landing page, which explains the project, and `terminal/index.html` is the terminal itself. Each is a single, self-contained file (no external files or CDNs). Add `?session`, `?fast` or `?offline` to the terminal address. Pick a
market packet, press **Run the six bots**, and watch Scout, News, Sentiment, Charts, the Risk gate, and
Decision work through it, ending in a final call of `BUY — SIMULATION ONLY` (▲ long), `SELL/SHORT — SIMULATION ONLY` (▼ short), `WAIT`, or `SKIP`. A human
must review every result.

- **EXMP sample:** Risk reduces the simulated size to $100, and the call is WAIT.
- **EXMQ missing price:** Risk rejects, the Decision Bot does not run, and the call is SKIP.
- **Demo session:** a virtual balance (default $10,000) trades the tokens you pick.
  - Prices are **live public prices**: Coinbase Exchange first, then Kraken. No keys, no sign-in, no
    real orders.
  - Each round, all six bots analyse real candles for the next token. A BUY opens a virtual long and
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
  - A paused Decision bot makes no new trades.
- Virtual balance · live public prices · no real orders · not financial advice.

The page uses the Alpha Hive look: amber and honey on charcoal, a honeycomb of hexagonal bot panels, and
worker-bee particles that fly along shared flight paths and swarm into the Risk gate and Decision. Scout
runs a hex-pulse scan, honey drips down the background, and the final call arrives with a countdown, an amber glitch and a hexagon burst.
The **Motion** button in the header switches to reduced motion, and the page starts that way when your
device asks for less motion.

The rest of this repository is the full Python project the page mirrors: six bots, schema checks with one
retry, a Risk gate enforced in code, an offline mock, an optional Grok (xAI) provider, a Flask browser
UI, and 12 safety tests. See [PROJECT_README.md](PROJECT_README.md) for setup on Mac, Linux, and Windows:

```
cp .env.example .env
pip install -r requirements.txt
python run_demo.py        # terminal run
python -m pytest -q       # 12 safety tests
python web/app.py         # browser UI at http://127.0.0.1:8642
```
