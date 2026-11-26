"""Accuracy scoreboard: every round logged, outcomes and would-have hit rates scored."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import scoreboard as sb  # noqa: E402
from agents.base import MockProvider, load_config, load_market, load_risk_rules  # noqa: E402
from agents.pipeline import run_pipeline  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    c = copy.deepcopy(load_config())
    c["reports_dir"] = str(tmp_path / "reports")
    c["logs_dir"] = str(tmp_path / "logs")
    return c


@pytest.fixture
def packet():
    return load_market(ROOT / "data" / "sample_market.json")


def test_every_round_is_logged_with_views_and_risk(packet, cfg):
    for _ in range(2):
        rep = run_pipeline(packet, MockProvider(), cfg, load_risk_rules())
    rows = sb.load(Path(cfg["reports_dir"]) / "scoreboard.jsonl")
    assert len(rows) == 2
    r = rows[-1]
    assert r["run_id"] == rep["run_id"] and r["token"] == packet["market"]
    assert r["call"] in ("LONG", "SHORT", "WAIT", "SKIP")
    assert set(r["views"]) == {"scout", "news", "sentiment", "charts"}
    assert r["risk"] == rep["outputs"]["risk"]["status"]
    assert r["price_start"] == packet["price"]["last"] and r["outcome"] == "pending"


def test_scoreboard_can_be_switched_off(packet, cfg):
    cfg["scoreboard"] = False
    run_pipeline(packet, MockProvider(), cfg, load_risk_rules())
    assert not (Path(cfg["reports_dir"]) / "scoreboard.jsonl").exists()


def test_call_key_and_leans_match_terminal_rules():
    assert sb.call_key("BUY — SIMULATION ONLY") == "LONG"
    assert sb.call_key("SELL/SHORT — SIMULATION ONLY") == "SHORT"
    assert sb.call_key("WAIT") == "WAIT" and sb.call_key(None) == "SKIP"
    assert sb.lean("news", {"positive_events": [1, 2], "negative_events": [1]}) == "bull"
    assert sb.lean("sentiment", {"sentiment": "neutral", "score": -25}) == "bear"
    assert sb.lean("sentiment", {"sentiment": "neutral", "score": 5}) == "neu"
    assert sb.lean("charts", {"trend": "up"}) == "bull" and sb.lean("charts", {"trend": "sideways"}) == "neu"


def test_move_threshold_counts_tiny_moves_as_flat():
    assert sb.move_of(100, 100.04) == 0      # 0.04% < 0.05%
    assert sb.move_of(100, 100.06) == 1
    assert sb.move_of(100, 99.9) == -1
    assert sb.move_of(None, 100) is None


def _rec(call, views, rid):
    rep = {"run_id": rid, "market": "BTC-USD", "final_result": call, "outputs": {
        "news": {"positive_events": ["a"] if views[0] == "bull" else [], "negative_events": ["b"] if views[0] == "bear" else []},
        "sentiment": {"sentiment": {"bull": "positive", "bear": "negative"}.get(views[1], "neutral"), "score": 0},
        "charts": {"trend": {"bull": "up", "bear": "down"}.get(views[2], "sideways")},
        "risk": {"status": "approve"}}}
    return rep


def test_trade_outcome_pnl_R_and_would_have_hits(tmp_path):
    path = tmp_path / "scoreboard.jsonl"
    sb.log_round(_rec("BUY — SIMULATION ONLY", ("bull", "bear", "neu"), "r1"), {"price": {"last": 100}}, path)
    out = sb.record_outcome(path, "r1", 104, outcome="target hit", entry=100, stop=98, target=104,
                            exit=104, pnl=20.0, risk_usd=10.0)
    assert out["outcome"] == "target hit" and out["R"] == 2.0 and out["move"] == 1
    assert out["hits"] == {"news": 1, "sentiment": 0, "charts": None, "queen": 1}


def test_wait_round_scores_would_have_direction_without_a_trade(tmp_path):
    path = tmp_path / "scoreboard.jsonl"
    sb.log_round(_rec("WAIT", ("bear", "bear", "bull"), "w1"), {"price": {"last": 50}}, path)
    out = sb.record_outcome(path, "w1", 49)          # price fell over the window
    assert out["outcome"] == "no trade" and out["pnl"] == 0.0 and out["R"] is None
    assert out["hits"] == {"news": 1, "sentiment": 1, "charts": 0, "queen": None}
    s = sb.summary(sb.load(path))
    assert s["trades"] == 0 and s["bots"]["news"]["n"] == 1


def test_summary_win_rate_avg_R_and_small_sample_warning(tmp_path):
    path = tmp_path / "scoreboard.jsonl"
    for i, (px, pnl) in enumerate([(104, 20.0), (98, -10.0), (104, 20.0)]):
        sb.log_round(_rec("BUY — SIMULATION ONLY", ("bull", "neu", "bull"), f"t{i}"), {"price": {"last": 100}}, path)
        sb.record_outcome(path, f"t{i}", px, outcome="target hit" if pnl > 0 else "stop hit",
                          entry=100, stop=98, target=104, exit=px, pnl=pnl, risk_usd=10.0)
    s = sb.summary(sb.load(path))
    assert s["trades"] == 3 and s["wins"] == 2
    assert s["win_rate"] == pytest.approx(2 / 3) and s["avg_R"] == pytest.approx(1.0)
    assert s["pnl"] == 30.0 and s["small_sample"] is True
    assert s["bots"]["charts"] == {"n": 3, "hits": 2, "hit_rate": pytest.approx(2 / 3), "small_sample": True}
    assert sb.summary(sb.load(path), venue="crucial-demo")["rounds"] == 0


def test_terminal_has_scoreboard_panel_csv_reset_and_venues():
    html = (ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    for needle in ('id="scoreBox"', 'id="scoreCsv"', 'id="scoreReset"', 'value="crucial-demo"', 'value="live"',
                   "alphaHive.scoreboard.v1", "FLAT=0.0005", "MIN_N=30", "not financial advice"):
        assert needle in html, needle
