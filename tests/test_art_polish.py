"""Smoke checks for event icons and town portraits."""

from __future__ import annotations

from art.embed_event_icons import event_icon_map
from art.embed_portraits import portrait_map


def test_event_icons_cover_feed_kinds() -> None:
    icons = event_icon_map()
    for key in ("speech", "attack", "death", "reflection", "world", "goal", "goalx"):
        assert key in icons
        assert icons[key].startswith("data:image/jpeg;base64,")


def test_hakoniwa_and_seahaven_portraits() -> None:
    for scen in ("hakoniwa", "seahaven"):
        m = portrait_map(scen)
        for aid in ("mei_yi", "chen_yuan", "lin_shu", "wang_hao", "guo_bo", "su_qing"):
            assert aid in m, f"{scen} missing {aid}"


def test_tempest_portraits() -> None:
    m = portrait_map("tempest")
    for aid in ("shen_xi", "shi_lei", "fang_lan", "a_qian", "a_wang", "qing_he", "gu_chao"):
        assert aid in m, f"tempest missing {aid}"


def test_tempest_art_icons() -> None:
    from art.embed_icons import icon_map

    m = icon_map("tempest")
    for aid in ("zhen_chao_li", "feng_zha", "wang_chao", "ji_feng_bu", "an_min_zhou", "hao_ling"):
        assert aid in m, f"tempest icon missing {aid}"
        assert m[aid].startswith("data:image/jpeg;base64,")
