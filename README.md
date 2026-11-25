# Six-Bot AI Trading Floor (simulation)

**Live demo:** https://yogibear1323.github.io/six-bot-trading-terminal/

> Educational simulation with fictional example data. It places no real orders, connects to no trading
> account, and is not financial advice.

`index.html` is a single, self-contained page (no external files or CDNs, so it works offline). Pick a
market packet, press **Run the six bots**, and watch Scout, News, Sentiment, Charts, the Risk gate, and
Decision work through it, ending in a final call of `BUY — SIMULATION ONLY`, `WAIT`, or `SKIP`. A human
must review every result.

- **EXMP sample:** Risk reduces the simulated size to $100, and the call is WAIT.
- **EXMQ missing price:** Risk rejects, the Decision Bot does not run, and the call is SKIP.

The rest of this repository is the full Python project the page mirrors: six bots, schema checks with one
retry, a Risk gate enforced in code, an offline mock, an optional Grok (xAI) provider, a Flask browser
UI, and 11 safety tests. See [PROJECT_README.md](PROJECT_README.md) for setup on Mac, Linux, and Windows:

```
cp .env.example .env
pip install -r requirements.txt
python run_demo.py        # terminal run
python -m pytest -q       # 11 safety tests
python web/app.py         # browser UI at http://127.0.0.1:8642
```
