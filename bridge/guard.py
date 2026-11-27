"""Server-side hard limits: kill switch, daily loss stop and pre-trade checks.

The kill switch can be engaged over HTTP (POST /kill) or with KILL_SWITCH=1, but it can
only be released from the machine itself: `python -m bridge release-kill`.
The daily loss stop latches for the rest of the UTC day once hit.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


class Blocked(Exception):
    """An order refused by a hard limit. .code is a short machine-readable reason."""
    def __init__(self, code: str, message: str):
        super().__init__(message); self.code = code


def utc_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc).strftime("%Y-%m-%d")


class GuardState:
    def __init__(self, state_dir: Path, limits, kill_env: bool = False, clock=time.time, day_name: str = "daily.json"):
        self.dir = Path(state_dir); self.limits = limits; self.kill_env = kill_env; self.clock = clock
        self.kill_file = self.dir / "KILL_SWITCH"; self.day_file = self.dir / day_name

    # ---- kill switch ----
    def kill_engaged(self) -> bool:
        return self.kill_env or self.kill_file.exists()

    def engage_kill(self, reason: str = "") -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.kill_file.write_text(json.dumps({"engaged_at": utc_day(self.clock()), "ts": self.clock(),
                                              "reason": reason[:200]}), encoding="utf-8")

    def release_kill(self) -> bool:
        if self.kill_file.exists():
            self.kill_file.unlink(); return True
        return False

    # ---- daily loss stop ----
    def _day(self) -> dict:
        try:
            d = json.loads(self.day_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        return d if d.get("day") == utc_day(self.clock()) else {}

    def daily_status(self, equity, today_net=None) -> dict:
        """Record the day's starting equity on first sight; latch the stop once the loss hits the limit."""
        d = self._day()
        if not d and isinstance(equity, (int, float)):
            d = {"day": utc_day(self.clock()), "start_equity": float(equity), "stopped": False}
        start = d.get("start_equity")
        loss = 0.0
        if isinstance(start, (int, float)) and start > 0 and isinstance(equity, (int, float)):
            loss = max(0.0, start - float(equity))
        if isinstance(today_net, (int, float)) and today_net < 0:
            loss = max(loss, -float(today_net))
        loss_pct = (loss / start * 100) if start else 0.0
        if start and loss_pct >= self.limits.daily_loss_stop_pct:
            d["stopped"] = True
        usd_cap = getattr(self.limits, "daily_loss_stop_usd", None)
        if usd_cap and loss >= usd_cap:
            d["stopped"] = True
        if d:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.day_file.write_text(json.dumps(d), encoding="utf-8")
        return {"day": d.get("day"), "start_equity": start, "loss_usd": round(loss, 2),
                "loss_pct": round(loss_pct, 3), "limit_pct": self.limits.daily_loss_stop_pct,
                "limit_usd": usd_cap, "stopped": bool(d.get("stopped"))}

    # ---- pre-trade ----
    def pre_trade(self, equity, today_net, open_positions: int) -> dict:
        if self.kill_engaged():
            raise Blocked("kill_switch", "Kill switch is engaged: no new orders. Release it on this machine with "
                                         "`python -m bridge release-kill`.")
        daily = self.daily_status(equity, today_net)
        if daily["stopped"]:
            cap = f"{daily['loss_pct']}% >= {daily['limit_pct']}%"
            if daily.get("limit_usd") and daily["loss_usd"] >= daily["limit_usd"]:
                cap = f"${daily['loss_usd']} >= ${daily['limit_usd']}"
            raise Blocked("daily_loss_stop", f"Daily loss stop hit ({cap}): no new orders until the next UTC day.")
        if open_positions >= self.limits.max_open_positions:
            raise Blocked("max_open_positions", f"{open_positions} position(s) open; the limit is "
                                                f"{self.limits.max_open_positions}.")
        return daily
