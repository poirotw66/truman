"""Tempest replay UX should not read as jianghu combat day."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "replay" / "template.html").read_text(encoding="utf-8")
DEMO = (ROOT / "truman" / "demo" / "static" / "index.html").read_text(encoding="utf-8")


def test_replay_has_tempest_clock_and_storm_navigation() -> None:
    assert "潮訊收緊" in TEMPLATE
    assert "kind: \"storm\"" in TEMPLATE or "kind: 'storm'" in TEMPLATE
    assert "上一關鍵刻" in TEMPLATE
    assert "function deathCause" in TEMPLATE
    assert "被暴潮捲走" in TEMPLATE


def test_demo_scenario_chrome_switches_away_from_hengshan() -> None:
    assert "SCENARIO_UI" in DEMO
    assert "scenesJianghu" in DEMO
    assert "cast/tempest.default.json" in DEMO
    assert "syncScenarioChrome" in DEMO


def test_hd2d_figures_draw_onto_scene_canvas() -> None:
    """Figures must land on offscreen `sc` so DOF/bloom composite does not wipe them."""
    assert "drawFigure(sc," in TEMPLATE
    assert "drawFigure(bx," not in TEMPLATE
    assert "sc.setLineDash(dash)" in TEMPLATE
    assert "bx.setLineDash(dash)" not in TEMPLATE
