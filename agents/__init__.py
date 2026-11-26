"""Six-bot research team, organised as a hive. Educational simulation only.

The Queen (bot key "decision") owns each round: she tasks Scout, dispatches the workers
(News, Sentiment, Charts), sends the proposal to Risk and makes the final call. Risk is the
hive's guard and answers to no one: the Queen cannot override or bypass it.
"""
BOT_ORDER = ["scout", "news", "sentiment", "charts", "risk", "decision"]
QUEEN = "decision"
SCOUT = "scout"
WORKERS = ["news", "sentiment", "charts"]
GUARD = "risk"
BOT_TITLES = {"scout": "Scout", "news": "News", "sentiment": "Sentiment", "charts": "Charts",
              "risk": "Risk", "decision": "Queen"}
