"""점수 공식 검증 — 실제 API를 호출하지 않는다."""
import math
from datetime import date

from src.classify.rules import keyword_field
from src.scoring.normalize import percentiles, weighted
from src.scoring.score import period_windows


def test_percentiles_log_rank_and_ties():
    p = percentiles({"a": 0, "b": 10, "c": 10, "d": 1000})
    assert p["a"] < p["b"] == p["c"] < p["d"]
    assert math.isclose(p["a"], 12.5) and math.isclose(p["d"], 87.5)


def test_constant_group_is_missing():
    assert percentiles({"a": 0, "b": 0, "c": 0}) == {}
    assert percentiles({"a": None, "b": 5}) == {}


def test_missing_weight_redistributed():
    pcts = {"x": {"i": 80.0}, "y": {}, "z": {"i": 20.0}}
    sc, cover = weighted(pcts, {"x": 0.6, "y": 0.2, "z": 0.2}, "i")
    assert math.isclose(sc, (0.6 * 80 + 0.2 * 20) / 0.8)
    assert math.isclose(cover, 0.8)


def test_period_windows():
    w = period_windows(date(2026, 10, 22))
    assert w["weekly"]["start"] == date(2026, 10, 15) and w["weekly"]["end"] == date(2026, 10, 21)
    assert w["monthly"]["start"] == date(2026, 9, 1) and w["monthly"]["end"] == date(2026, 9, 30)
    assert w["yearly"]["start"] == date(2026, 1, 1)
    w2 = period_windows(date(2027, 1, 3))
    assert w2["monthly"]["start"] == date(2026, 12, 1)


def test_keyword_field_rules():
    assert keyword_field("Whisper speech recognition toolkit")[0] == "speech"
    assert keyword_field("vision-language model for documents")[0] == "multimodal"
    assert keyword_field("autonomous agent framework with tool use")[0] == "agents"
