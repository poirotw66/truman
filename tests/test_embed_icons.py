"""Smoke check for art skill icon embedding."""

from __future__ import annotations

from truman.world import arts as arts_mod

from art.embed_icons import icon_map


def test_icon_map_covers_catalog() -> None:
    icons = icon_map("jianghu")
    missing = [art_id for art_id in arts_mod.CATALOG if art_id not in icons]
    assert missing == []
    for uri in icons.values():
        assert uri.startswith("data:image/jpeg;base64,")
        assert len(uri) > 500
