"""把 art/portraits 底下的立繪縮圖壓成 data URI，給兩個網頁內嵌。

網頁要離線可開、單檔可寄，所以圖不能外連——一律 base64 塞進 HTML。
原圖 896×1200 PNG 每張約 700 KB，六張就 4 MB，直接塞會讓網頁肥到不能看；
這裡縮到 480 寬、轉 JPEG，六張加起來約 300–400 KB。

    python art/embed_portraits.py          # 看看縮完多大
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTRAITS = Path(__file__).with_name("portraits")


def portrait_map(scenario: str, px: int = 480, quality: int = 82) -> dict[str, str]:
    """{agent_id: data URI}。沒有圖就回空的，網頁會自動退回程式畫的像素立繪。"""
    d = PORTRAITS / scenario
    if not d.exists():
        return {}
    try:
        from PIL import Image
    except ImportError:
        print("  ⚠ 沒裝 Pillow，立繪不會內嵌（pip install pillow）")
        return {}
    out: dict[str, str] = {}
    files = sorted(d.glob("*.png")) + sorted(d.glob("*.jpg"))
    seen: set[str] = set()
    for p in files:
        if p.stem in seen:
            continue
        seen.add(p.stem)
        im = Image.open(p).convert("RGB")
        h = round(px * im.height / im.width)
        im = im.resize((px, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        out[p.stem] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return out


if __name__ == "__main__":
    scen = sys.argv[1] if len(sys.argv) > 1 else "jianghu"
    m = portrait_map(scen)
    if not m:
        raise SystemExit(f"art/portraits/{scen} 底下沒有圖，先跑 python art/gen_portraits.py")
    total = 0
    for k, v in m.items():
        kb = len(v) / 1024
        total += kb
        print(f"  {k:16s} {kb:7.1f} KB")
    print(f"  {'合計':16s} {total:7.1f} KB（會加進每個網頁）")
