"""把事件流小圖示壓成 data URI（speech / attack / death …）。

沒有圖時回放頁退回程式畫的像素圖示。

    python art/embed_event_icons.py
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

EVENTS = Path(__file__).with_name("icons") / "events"
KEYS = ("speech", "attack", "death", "reflection", "world", "interact", "goal", "goalx")


def event_icon_map(*, px: int = 64, quality: int = 80) -> dict[str, str]:
    if not EVENTS.exists():
        return {}
    try:
        from PIL import Image
    except ImportError:
        print("  ⚠ 沒裝 Pillow，事件圖示不會內嵌")
        return {}
    out: dict[str, str] = {}
    for key in KEYS:
        p = EVENTS / f"{key}.jpg"
        if not p.exists():
            p = EVENTS / f"{key}.png"
        if not p.exists():
            continue
        im = Image.open(p).convert("RGB").resize((px, px), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        out[key] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return out


if __name__ == "__main__":
    m = event_icon_map()
    if not m:
        raise SystemExit("art/icons/events 底下沒有圖")
    total = 0.0
    for k, v in m.items():
        kb = len(v) / 1024
        total += kb
        print(f"  {k:12s} {kb:6.1f} KB")
    print(f"  {'合計':12s} {total:6.1f} KB")
