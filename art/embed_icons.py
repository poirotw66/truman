"""把 art/icons 底下的絕技圖示壓成 data URI，給回放頁內嵌。

圖示是 1:1 方圖，UI 只需要小尺寸——統一縮到 96 寬 JPEG。
十五張加起來大約 100 KB。

    python art/embed_icons.py
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = Path(__file__).with_name("icons")


def icon_map(
    scenario: str = "jianghu",
    *,
    px: int = 96,
    quality: int = 78,
) -> dict[str, str]:
    try:
        from PIL import Image
    except ImportError:
        print("  ⚠ 沒裝 Pillow，絕技圖示不會內嵌（pip install pillow）")
        return {}

    def embed_icon(p: Path) -> str:
        im = Image.open(p).convert("RGB")
        im = im.resize((px, px), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    # Priority:
    # 1) icons/<scenario> for requested scenario variants
    # 2) other scenario folders as fallback so {art_id: uri} covers the whole catalog
    out: dict[str, str] = {}
    preferred_dir = ICONS / scenario
    if preferred_dir.exists():
        preferred_files = sorted(preferred_dir.glob("*.jpg")) + sorted(preferred_dir.glob("*.png"))
        for p in preferred_files:
            out[p.stem] = embed_icon(p)

    other_dirs = sorted([p for p in ICONS.iterdir() if p.is_dir() and p.name != scenario])
    for d in other_dirs:
        for p in sorted(d.glob("*.jpg")) + sorted(d.glob("*.png")):
            out.setdefault(p.stem, embed_icon(p))
    return out


if __name__ == "__main__":
    scen = sys.argv[1] if len(sys.argv) > 1 else "jianghu"
    m = icon_map(scen)
    if not m:
        raise SystemExit(f"art/icons/{scen} 底下沒有圖")
    total = 0.0
    for k, v in m.items():
        kb = len(v) / 1024
        total += kb
        print(f"  {k:24s} {kb:6.1f} KB")
    print(f"  {'合計':24s} {total:6.1f} KB")
