"""把 art/scenes 底下的場景／關鍵美術壓成 data URI，給回放頁與 Demo 內嵌。

網頁要離線可開、單檔可寄，所以圖不能外連——一律 base64 塞進 HTML。
原圖約 1.5 MB（八張 JPEG），直接塞會讓網頁肥到不能看；
這裡再縮一輪：關鍵美術 960 寬、場景 560 寬，合計約 400–500 KB。

    python art/embed_scenes.py          # 看看縮完多大
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENES = Path(__file__).with_name("scenes")

# 檔名 stem → DATA.scenes 的鍵。
# 江湖地名對齊 scenarios/jianghu.py；嵐潮地名對齊 scenarios/tempest.py。
SCENE_KEYS: dict[str, str] = {
    "keyart_hengshan": "keyart",
    "keyart_tempest": "keyart",
    "keyart_night": "night",
    "scene_liufu": "劉府",
    "scene_market": "市集",
    "scene_tavern": "迴雁樓",
    "scene_plaza": "演武場",
    "scene_gate": "城門",
    "scene_shrine": "荒祠",
    "scene_yard": "後院",
    "scene_qunyu": "群玉院",
    "scene_temple": "城隍廟",
    # tempest · 嵐潮鎮
    "scene_gaodi": "高地",
    "scene_cunzhang": "村長宅",
    "scene_miao": "鎮廟",
    "scene_guangchang": "廣場",
    "scene_tiepu": "鐵鋪",
    "scene_yushi": "漁市",
    "scene_liangcang": "糧倉",
    "scene_haidi": "海堤",
    "scene_yugang": "漁港",
}


def scene_map(
    scenario: str,
    *,
    keyart_px: int = 800,
    scene_px: int = 480,
    quality: int = 72,
) -> dict[str, str]:
    """{key: data URI}。沒有圖就回空的，網頁自動退回像素城底圖。"""
    d = SCENES / scenario
    if not d.exists():
        return {}
    try:
        from PIL import Image
    except ImportError:
        print("  ⚠ 沒裝 Pillow，場景圖不會內嵌（pip install pillow）")
        return {}
    out: dict[str, str] = {}
    for p in sorted(d.glob("*.jpg")) + sorted(d.glob("*.png")):
        key = SCENE_KEYS.get(p.stem)
        if key is None:
            continue
        px = keyart_px if key in ("keyart", "night") else scene_px
        im = Image.open(p).convert("RGB")
        h = round(px * im.height / im.width)
        im = im.resize((px, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        out[key] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return out


if __name__ == "__main__":
    scen = sys.argv[1] if len(sys.argv) > 1 else "jianghu"
    m = scene_map(scen)
    if not m:
        raise SystemExit(f"art/scenes/{scen} 底下沒有圖")
    total = 0.0
    for k, v in m.items():
        kb = len(v) / 1024
        total += kb
        print(f"  {k:12s} {kb:7.1f} KB")
    print(f"  {'合計':12s} {total:7.1f} KB（會加進每個網頁）")
