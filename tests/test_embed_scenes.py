"""Smoke check for scene art embedding."""

from __future__ import annotations

from art.embed_scenes import SCENE_KEYS, scene_map


def test_scene_map_embeds_expected_keys() -> None:
    scenes = scene_map("jianghu")
    assert set(scenes) >= {
        "keyart", "night", "劉府", "市集", "城門", "後院", "群玉院", "城隍廟", "荒祠",
    }
    for key, uri in scenes.items():
        assert uri.startswith("data:image/jpeg;base64,")
        assert len(uri) > 1000


def test_scene_keys_cover_master_files() -> None:
    assert "keyart_hengshan" in SCENE_KEYS
    assert SCENE_KEYS["scene_liufu"] == "劉府"
    assert SCENE_KEYS["scene_temple"] == "城隍廟"
    assert SCENE_KEYS["keyart_tempest"] == "keyart"
    assert SCENE_KEYS["scene_haidi"] == "海堤"
    assert SCENE_KEYS["scene_miao"] == "鎮廟"


def test_tempest_scene_map_embeds_expected_keys() -> None:
    scenes = scene_map("tempest")
    assert set(scenes) >= {
        "keyart", "night", "高地", "村長宅", "鎮廟", "廣場", "鐵鋪", "漁市", "糧倉", "海堤", "漁港",
    }
    for uri in scenes.values():
        assert uri.startswith("data:image/jpeg;base64,")
        assert len(uri) > 1000
