"""Read-only DEMO connection test: login, account info, instrument list. Places no orders.
Prints no credentials or tokens."""
from bridge.tradelocker import TradeLockerClient, TradeLockerError

WATCH = ("BTCUSD", "ETHUSD", "US30")


def run_check(s, client=None, out=print) -> int:
    missing = [k for k, v in (("TRADELOCKER_EMAIL", s.email), ("TRADELOCKER_PASSWORD", s.password),
                              ("TRADELOCKER_SERVER", s.server)) if not v]
    if missing:
        out("missing in env / bridge/.env: " + ", ".join(missing)); return 2
    if s.env != "demo":
        out("check runs against the DEMO backend only (TRADELOCKER_ENV=demo)"); return 2
    c = client or TradeLockerClient(s.base_url, s.email, s.password, s.server, account_id=s.account_id,
                                    acc_num=s.acc_num, developer_api_key=s.developer_api_key)
    try:
        acct = c.login()
        out(f"login OK · backend {s.base_url} · server {s.server}")
        accts = c.accounts()
        out(f"accounts: {len(accts)}; using id {acct.get('id')} (accNum {acct.get('accNum')}, "
            f"{acct.get('currency')}, {acct.get('status')})")
        state = c.account_state()
        out(f"balance {state.get('balance')} · equity {state.get('projectedBalance', state.get('balance'))}")
        items = c.instruments()
        out(f"instruments: {len(items)}")
        for w in WATCH:
            try:
                i = c.find_instrument(w)
                d = c.instrument_details(i)
                out(f"  {w}: {i['name']} id {i['tradableInstrumentId']} · lotSize {d.get('lotSize')} · "
                    f"minLot {d.get('minLot')} · lotStep {d.get('lotStep')} · leverage {d.get('leverage')}")
            except TradeLockerError as e:
                out(f"  {w}: not found ({e})")
        out("no orders were placed")
        return 0
    except TradeLockerError as e:
        out(f"connection test failed: {e}"); return 1
