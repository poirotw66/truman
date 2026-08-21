"""Epilogue digest helpers — no network."""

from __future__ import annotations

from truman.director.epilogue import digest_events, normalize_epilogue


def test_digest_includes_death_and_storm() -> None:
    events = [
        {"type": "run_start", "tick": 0, "data": {"run_id": "tX", "scenario": "tempest"}},
        {"type": "death", "tick": 72, "data": {"name": "阿德", "killed_by": "暴潮", "agent": "gu_chao"}},
        {"type": "storm", "tick": 72, "data": {"outcome": "lost", "text": "（滅村的邊緣。）"}},
        {"type": "run_summary", "tick": 96, "data": {}},
    ]
    scenario, run_id, text = digest_events(events)
    assert scenario == "tempest"
    assert run_id == "tX"
    assert "暴潮" in text or "滅村" in text
    assert "阿德" in text


def test_normalize_requires_all_fields() -> None:
    out = normalize_epilogue({"label": "金盆成了", "blurb": "短。", "commentary": "長評。"})
    assert out["label"] == "金盆成了"
    try:
        normalize_epilogue({"label": "x", "blurb": "", "commentary": "y"})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
