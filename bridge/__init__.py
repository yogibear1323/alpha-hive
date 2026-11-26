"""Alpha Hive broker bridge: a small local FastAPI service between the hive and a
Crucial Markets TradeLocker account. DEMO-first.

Everything that can touch a broker lives in this package. The research agents
(agents/), run_demo.py and the Flask UI (web/*.py) contain no order code, and a test
keeps it that way. The bridge only accepts an order after the Risk guard approved it,
and it enforces its own hard limits server-side (risk per trade, position size, daily
loss stop, open positions, leverage, kill switch).
"""
