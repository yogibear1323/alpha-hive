"""Queen Bot: the boss of the hive (bot key "decision", formerly the Decision Bot).

The Queen owns the round. She tasks Scout, dispatches the three worker bots (News, Sentiment,
Charts), sends the hive's proposal to Risk, and makes the final call. Educational simulation only.

HARD RULE: Risk is the hive's guard and answers to no one. The Queen cannot override, skip or
soften the Risk gate:
  * Risk always runs, on its own copy of the research, before the Queen can make a call.
  * A Risk "reject" ends the round with SKIP; the Queen's call is never made.
  * Under a Risk "reduce" a long or short is downgraded to WAIT in code (never flipped).
  * The Queen only ever sees a copy of the Risk verdict; if the stored verdict changes during her
    call the round stops with an error.
  * A config that tries to let the Queen overrule Risk is refused.

Her call returns 'BUY — SIMULATION ONLY' (long), 'SELL/SHORT — SIMULATION ONLY' (short), 'WAIT'
or 'SKIP'. human_review_required is forced to true in code. Nothing here places an order.
"""
from __future__ import annotations

import copy

from agents import charts, news, risk, scout, sentiment
from agents.base import DECISIONS, DIRECTIONAL, BotError, RiskRejectedError, call_bot

NAME = "decision"          # bot key kept for prompts, reports and the UI; shown as "Queen"
TITLE = "Queen"
SCOUT = "scout"
WORKERS = ("news", "sentiment", "charts")
GUARD = "risk"

SCHEMA = {
    "decision": {"type": "str", "enum": DECISIONS},
    "reasons": {"type": "list", "items": "str"},
    "conflicting_signals": {"type": "list", "items": "str"},
    "missing_information": {"type": "list", "items": "str"},
    "invalidation_conditions": {"type": "list", "items": "str"},
    "human_review_required": {"type": "bool"},
}

GUARD_RULE = ("Risk is the hive's guard and answers to no one: the Queen cannot override or "
              "bypass a Risk reject or reduce.")


class GuardIndependenceError(BotError):
    """Raised when config or code tries to let the Queen overrule the Risk guard."""


def run(packet: dict, research: dict, risk_result: dict, provider, logger=None) -> dict:
    """The Queen's call. Refuses to run unless Risk approved or reduced."""
    status = (risk_result or {}).get("status")
    if status not in ("approve", "reduce"):
        raise RiskRejectedError(f"Queen refused to make a call: Risk status is '{status}'.")
    payload = {"market": packet.get("market"), "research": research, "risk": risk_result}
    out = call_bot(NAME, SCHEMA, payload, provider, logger)
    out["human_review_required"] = True
    if out["decision"] in DIRECTIONAL and status != "approve":
        side = "BUY" if out["decision"] == DECISIONS[0] else "SELL/SHORT"
        out["decision"] = "WAIT"
        out["reasons"] = out["reasons"] + [f"Code downgraded {side} to WAIT: Risk did not fully approve."]
    return out


def check_hierarchy(config: dict | None) -> dict:
    """Validate config['hierarchy']; the guard can never be overruled."""
    h = dict((config or {}).get("hierarchy") or {})
    if h.get("queen_can_overrule_guard"):
        raise GuardIndependenceError(GUARD_RULE + " Set hierarchy.queen_can_overrule_guard to false.")
    if h.get("guard", GUARD) != GUARD:
        raise GuardIndependenceError(GUARD_RULE + " The guard must be the Risk bot.")
    if h.get("queen", NAME) != NAME:
        raise BotError("hierarchy.queen must be 'decision' (the Queen Bot).")
    return {"queen": NAME, "scout": SCOUT, "workers": list(WORKERS), "guard": GUARD,
            "queen_can_overrule_guard": False}


class Queen:
    """Runs one round in the hive's order and keeps a delegation ledger."""

    MODULES = {"scout": scout, "news": news, "sentiment": sentiment, "charts": charts}
    LABELS = {"scout": "Scout", "news": "News", "sentiment": "Sentiment", "charts": "Charts",
              "risk": "Risk", "decision": TITLE}
    STEP = {"scout": 1, "news": 2, "sentiment": 3, "charts": 4, "risk": 5, "decision": 6}

    def __init__(self, provider, rules: dict, config: dict, logger=None, emit=None):
        self.hierarchy = check_hierarchy(config)
        self.provider, self.rules, self.config, self.logger = provider, rules, config, logger
        self.emit = emit or (lambda ev: None)
        self.ledger: list[dict] = []

    def _note(self, action: str, bot: str):
        self.ledger.append({"action": action, "bot": bot})
        self.emit({"type": "queen", "action": action, "bot": bot, "label": self.LABELS[bot]})
        if self.logger:
            self.logger.log(f"Queen: {action}" + ("" if bot == NAME else f" -> {self.LABELS[bot]}"))

    def _task(self, key: str, action: str, packet: dict, outputs: dict):
        step, label = self.STEP[key], self.LABELS[key]
        self._note(action, key)
        self.emit({"type": "step_start", "step": step, "bot": key, "label": label})
        outputs[key] = self.MODULES[key].run(packet, self.provider, self.logger)
        if self.logger:
            self.logger.log(f"[{step}/6] {label} Bot complete")
        self._note("report", key)
        self.emit({"type": "step_done", "step": step, "bot": key, "label": label, "output": outputs[key]})

    def hold_round(self, packet: dict, outputs: dict) -> tuple[str, str | None]:
        """Returns (final_result, stopped_at). Fills outputs in place."""
        self._task(SCOUT, "task", packet, outputs)
        for key in WORKERS:
            self._task(key, "dispatch", packet, outputs)

        research = {k: outputs[k] for k in (SCOUT, *WORKERS)}
        self._note("propose", GUARD)
        self.emit({"type": "step_start", "step": 5, "bot": GUARD, "label": "Risk"})
        verdict = risk.run(packet, copy.deepcopy(research), self.provider, self.rules, self.config, self.logger)
        outputs[GUARD] = verdict
        sealed = copy.deepcopy(verdict)
        if self.logger:
            self.logger.log(f"[5/6] Risk Bot complete status={verdict['status']}")
        self._note("verdict", GUARD)
        self.emit({"type": "step_done", "step": 5, "bot": GUARD, "label": "Risk", "output": verdict})

        if verdict["status"] == "reject":
            self._note("stand_down", NAME)
            if self.logger:
                self.logger.log("Risk rejected: the Queen stands down, no call is made")
            self.emit({"type": "stopped", "step": 6, "reason": "Risk rejected - the Queen makes no call"})
            return "SKIP", "risk"

        self._note("call", NAME)
        self.emit({"type": "step_start", "step": 6, "bot": NAME, "label": TITLE})
        call = run(packet, copy.deepcopy(research), copy.deepcopy(sealed), self.provider, self.logger)
        if outputs[GUARD] != sealed:
            raise GuardIndependenceError(GUARD_RULE + " The Risk verdict changed during the Queen's call.")
        outputs[NAME] = call
        if self.logger:
            self.logger.log("[6/6] Queen's call complete")
        self.emit({"type": "step_done", "step": 6, "bot": NAME, "label": TITLE, "output": call})
        return call["decision"], None
