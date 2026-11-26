"""Bridge settings, read only from the environment / bridge/.env. Secrets never leave here."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
BASE_URLS = {"demo": "https://demo.tradelocker.com/backend-api",
             "live": "https://live.tradelocker.com/backend-api"}
LIVE_CONFIRM_PHRASE = "I UNDERSTAND THIS IS REAL MONEY"

# Code ceilings: .env may tighten these limits, never loosen them past these values.
CEILINGS = {"max_risk_pct_per_trade": 2.0, "max_position_pct_of_balance": 100.0,
            "daily_loss_stop_pct": 10.0, "max_open_positions": 5, "account_max_leverage": 30.0}
DEFAULT_SYMBOLS = {"BTC": "BTCUSD", "ETH": "ETHUSD", "US30": "US30",
                   "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD"}
COINBASE_PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XLM": "XLM-USD",
                     "XRP": "XRP-USD", "DOGE": "DOGE-USD", "ADA": "ADA-USD", "AVAX": "AVAX-USD",
                     "LINK": "LINK-USD", "BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD",
                     "SOL-USD": "SOL-USD", "XLM-USD": "XLM-USD"}
COINBASE_LEVERAGE_CEILING = 1.0


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    max_risk_pct_per_trade: float = 0.5
    max_position_usd: float = 2000.0
    max_position_pct_of_balance: float = 20.0
    daily_loss_stop_pct: float = 2.0
    max_open_positions: int = 1
    account_max_leverage: float = 30.0
    max_entry_drift: float = 0.5   # broker price may differ from the round's entry by <= this x stop distance
    daily_loss_stop_usd: float | None = None  # optional dollar latch (used by Coinbase's $5 default)

    def validate(self) -> "Limits":
        for k, ceiling in CEILINGS.items():
            v = getattr(self, k)
            if not (v > 0):
                raise SettingsError(f"{k.upper()} must be > 0 (got {v})")
            if v > ceiling:
                raise SettingsError(f"{k.upper()}={v} is above the code ceiling {ceiling}")
        if not (self.max_position_usd > 0):
            raise SettingsError("MAX_POSITION_USD must be > 0")
        if not (0 < self.max_entry_drift <= 1):
            raise SettingsError("MAX_ENTRY_DRIFT must be in (0, 1]")
        if self.daily_loss_stop_usd is not None and not (self.daily_loss_stop_usd > 0):
            raise SettingsError("DAILY_LOSS_STOP_USD must be > 0")
        return self


@dataclass(frozen=True)
class Settings:
    env: str = "demo"
    email: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    server: str = ""
    account_id: str = ""
    acc_num: str = ""
    developer_api_key: str = field(default="", repr=False)
    allow_live: bool = False
    bridge_token: str = field(default="", repr=False)
    port: int = 8765
    limits: Limits = field(default_factory=Limits)
    symbols: dict = field(default_factory=lambda: dict(DEFAULT_SYMBOLS))
    state_dir: Path = BRIDGE_DIR / "state"
    kill_switch_env: bool = False
    token_generated: bool = False
    broker: str = "tradelocker"
    coinbase_api_key_name: str = field(default="", repr=False)
    coinbase_api_private_key: str = field(default="", repr=False)
    coinbase_symbols: dict = field(default_factory=lambda: dict(COINBASE_PRODUCTS))

    host = "127.0.0.1"   # never bind to anything else

    @property
    def base_url(self) -> str:
        return BASE_URLS[self.env]

    @property
    def has_credentials(self) -> bool:
        if self.broker == "coinbase":
            return bool(self.coinbase_api_key_name and self.coinbase_api_private_key)
        return bool(self.email and self.password and self.server)

    @property
    def has_coinbase_credentials(self) -> bool:
        return bool(self.coinbase_api_key_name and self.coinbase_api_private_key)


def _flag(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _num(env, key, default, cast=float):
    raw = env.get(key)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except ValueError:
        raise SettingsError(f"{key} must be a number (got {raw!r})")


def load_settings(env: dict | None = None, dotenv_path: Path | None = BRIDGE_DIR / ".env") -> Settings:
    if env is None:
        if dotenv_path and Path(dotenv_path).exists():
            from dotenv import dotenv_values
            env = {**{k: v for k, v in dotenv_values(dotenv_path).items() if v is not None}, **os.environ}
        else:
            env = dict(os.environ)
    tl_env = (env.get("TRADELOCKER_ENV") or "demo").strip().lower()
    if tl_env not in BASE_URLS:
        raise SettingsError("TRADELOCKER_ENV must be 'demo' or 'live'")
    allow_live = _flag(env.get("ALPHA_HIVE_ALLOW_LIVE"))
    cb_key = (env.get("COINBASE_API_KEY_NAME") or env.get("COINBASE_API_KEY") or "").strip()
    cb_secret = env.get("COINBASE_API_PRIVATE_KEY") or env.get("COINBASE_API_SECRET") or ""
    if cb_secret:
        # Keep the raw value; CoinbaseClient unescapes PEM newlines.
        cb_secret = str(cb_secret)
    has_cb = bool(cb_key and str(cb_secret).strip())
    has_tl = bool((env.get("TRADELOCKER_EMAIL") or "").strip() and env.get("TRADELOCKER_PASSWORD")
                  and (env.get("TRADELOCKER_SERVER") or "").strip())
    raw_broker = (env.get("BRIDGE_BROKER") or "").strip().lower()
    if raw_broker in ("", "auto"):
        broker = "coinbase" if has_cb and not has_tl else "tradelocker"
    elif raw_broker in ("coinbase", "tradelocker"):
        broker = raw_broker
    else:
        raise SettingsError("BRIDGE_BROKER must be 'tradelocker' or 'coinbase'")
    if tl_env == "live" and not allow_live and broker != "coinbase":
        raise SettingsError("TRADELOCKER_ENV=live needs ALPHA_HIVE_ALLOW_LIVE=1 as well. Stay on demo until you have tested.")
    if broker == "coinbase":
        limits = Limits(
            max_risk_pct_per_trade=_num(env, "COINBASE_MAX_RISK_PCT_PER_TRADE",
                                        _num(env, "MAX_RISK_PCT_PER_TRADE", 0.5)),
            max_position_usd=_num(env, "COINBASE_MAX_POSITION_USD", 25.0),
            max_position_pct_of_balance=_num(env, "COINBASE_MAX_POSITION_PCT_OF_BALANCE",
                                             _num(env, "MAX_POSITION_PCT_OF_BALANCE", 20.0)),
            daily_loss_stop_pct=_num(env, "COINBASE_DAILY_LOSS_STOP_PCT",
                                     _num(env, "DAILY_LOSS_STOP_PCT", 2.0)),
            max_open_positions=_num(env, "COINBASE_MAX_OPEN_POSITIONS",
                                    _num(env, "MAX_OPEN_POSITIONS", 1, int), int),
            account_max_leverage=_num(env, "COINBASE_ACCOUNT_MAX_LEVERAGE", 1.0),
            max_entry_drift=_num(env, "MAX_ENTRY_DRIFT", 0.5),
            daily_loss_stop_usd=_num(env, "COINBASE_DAILY_LOSS_STOP_USD", 5.0),
        )
        if limits.account_max_leverage > COINBASE_LEVERAGE_CEILING:
            raise SettingsError(f"COINBASE_ACCOUNT_MAX_LEVERAGE={limits.account_max_leverage} is above the "
                                f"Coinbase spot ceiling {COINBASE_LEVERAGE_CEILING} (no margin or perps).")
        limits = limits.validate()
    else:
        limits = Limits(
            max_risk_pct_per_trade=_num(env, "MAX_RISK_PCT_PER_TRADE", 0.5),
            max_position_usd=_num(env, "MAX_POSITION_USD", 2000.0),
            max_position_pct_of_balance=_num(env, "MAX_POSITION_PCT_OF_BALANCE", 20.0),
            daily_loss_stop_pct=_num(env, "DAILY_LOSS_STOP_PCT", 2.0),
            max_open_positions=_num(env, "MAX_OPEN_POSITIONS", 1, int),
            account_max_leverage=_num(env, "ACCOUNT_MAX_LEVERAGE", 30.0),
            max_entry_drift=_num(env, "MAX_ENTRY_DRIFT", 0.5),
        ).validate()
    symbols = dict(DEFAULT_SYMBOLS)
    for pair in (env.get("SYMBOL_MAP") or "").split(","):
        if ":" in pair:
            a, b = pair.split(":", 1)
            symbols[a.strip().upper()] = b.strip()
    cb_symbols = dict(COINBASE_PRODUCTS)
    for pair in (env.get("COINBASE_SYMBOL_MAP") or "").split(","):
        if ":" in pair:
            a, b = pair.split(":", 1)
            cb_symbols[a.strip().upper()] = b.strip()
    token = (env.get("BRIDGE_TOKEN") or "").strip()
    generated = False
    if not token:
        token, generated = secrets.token_urlsafe(24), True
    elif len(token) < 16:
        raise SettingsError("BRIDGE_TOKEN must be at least 16 characters")
    return Settings(env=tl_env, email=(env.get("TRADELOCKER_EMAIL") or "").strip(),
                    password=env.get("TRADELOCKER_PASSWORD") or "",
                    server=(env.get("TRADELOCKER_SERVER") or "").strip(),
                    account_id=(env.get("TRADELOCKER_ACCOUNT_ID") or "").strip(),
                    acc_num=(env.get("TRADELOCKER_ACC_NUM") or "").strip(),
                    developer_api_key=(env.get("TRADELOCKER_DEVELOPER_API_KEY") or "").strip(),
                    allow_live=allow_live, bridge_token=token,
                    port=_num(env, "BRIDGE_PORT", 8765, int), limits=limits, symbols=symbols,
                    state_dir=Path(env.get("BRIDGE_STATE_DIR") or BRIDGE_DIR / "state"),
                    kill_switch_env=_flag(env.get("KILL_SWITCH")), token_generated=generated,
                    broker=broker, coinbase_api_key_name=cb_key,
                    coinbase_api_private_key=cb_secret, coinbase_symbols=cb_symbols)
