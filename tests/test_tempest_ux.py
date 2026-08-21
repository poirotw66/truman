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


def test_tempest_cast_look_reaches_replay_profiles() -> None:
    """嵐潮 HD-2D 小人要吃 cast look，不能整組掉回 PIXEL_FALLBACK。"""
    assert "c.look" in TEMPLATE
    assert "PIXEL_FALLBACK, look" in TEMPLATE
    assert "weapon === \"hammer\"" in (ROOT / "web" / "pixelart.js").read_text(encoding="utf-8")
    assert "acc === \"beads\"" in (ROOT / "web" / "pixelart.js").read_text(encoding="utf-8")


def test_tempest_offshore_boat_tiles_not_mapped_to_dirt() -> None:
    """外海甲板 o 必須畫成海上木板，不能被 TEMPEST_REPLAY_SYM 映射成泥地 y。"""
    from replay.build_frames import TEMPEST_REPLAY_SYM, replay_rows
    from scenarios import tempest as scen

    assert "o" not in TEMPEST_REPLAY_SYM
    pixel = (ROOT / "web" / "pixelart.js").read_text(encoding="utf-8")
    assert 'sym === "o"' in pixel
    rows = replay_rows(scen)
    assert any("o" in row for row in rows)
    assert all("~" in row or "o" in row or "#" in row for row in rows[-4:-1])
    assert "DATA.epilogue" in TEMPLATE
    assert "戲外導演" in TEMPLATE
    assert "details.epi" in TEMPLATE or "class=\"epi\"" in TEMPLATE
