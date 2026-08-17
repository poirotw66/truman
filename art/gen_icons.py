"""用 Gemini 影像模型產生絕技圖示（1:1），風格必須整組一致。

    python art/gen_icons.py --scenario tempest
    python art/gen_icons.py --scenario tempest --force
    python art/gen_icons.py --scenario tempest --only feng_zha

輸出 art/icons/<scenario>/<art_id>.jpg
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

MODEL = "gemini-3.1-flash-lite-image"
OUT = Path(__file__).with_name("icons")

STYLE = """\
A premium game skill icon, square 1:1 composition, centred subject filling most of the frame.

STYLE — apply identically to every icon in this set:
- Bold comic / manga inking with clear silhouette readable at 48px.
- Rich saturated colour, soft rim light, subtle halftone in shadows.
- Dark smoky or storm-gradient background; thin ornamental frame in muted brass / wet-iron.
- Single clear prop or ritual object — NO readable modern text, NO letters, NO watermarks.
- Taiwanese coastal folk / temple / fishing-village material culture (not wuxia swords).
"""

ANCHOR_NOTE = """\

STYLE REFERENCE: match the attached reference icon EXACTLY — same line weight, framing, lighting,
and finish. Different skill, same icon set. Do not copy the reference object — only the rendering style.
"""

# art_id → English subject (ids match truman.world.arts)
ICONS: dict[str, str] = {
    "zhen_chao_li": """\
SKILL ICON — 做海醮 (sea rite).
Ornate temple handbell and incense smoke rising over a small sea-god altar tray,
salt spray catching the rim light. Sacred urgency, indigo and ember accents.""",
    "feng_zha": """\
SKILL ICON — 焊水門 (seal the sluice).
Heavy iron sluice plate gripped by tongs, welding sparks and wet rivets,
cool storm-iron palette with a small spark accent.""",
    "wang_chao": """\
SKILL ICON — 探潮 (read the tide).
Brass hand-telescope / spyglass angled toward a distant white tide-line under a cloud wall,
salt crust and wind ribbons.""",
    "ji_feng_bu": """\
SKILL ICON — 飛毛腿 (fleet foot).
Worn canvas running shoes and a coiled rope whipping in typhoon wind,
motion lines, teal and mud colours.""",
    "an_min_zhou": """\
SKILL ICON — 穩陣 (steady the crowd).
A pair of calm open hands over a cloth herbal pouch and a small oil lamp,
soft sage light cutting panic — reassuring, not magical fireworks.""",
    "hao_ling": """\
SKILL ICON — 派工 (dispatch).
A silver whistle and a rain-spattered wooden tally board / dispatch ledger,
sea-green cord, authority without luxury.""",
}

DEFAULT_ANCHOR = {
    "tempest": "zhen_chao_li",
}


def generate(client, model: str, prompt: str, ref: bytes | None) -> tuple[bytes, str]:
    parts: list = [prompt + (ANCHOR_NOTE if ref else "")]
    if ref:
        parts.append(types.Part.from_bytes(data=ref, mime_type="image/png"))
    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio="1:1"),
    )
    resp = client.models.generate_content(model=model, contents=parts, config=cfg)
    um = getattr(resp, "usage_metadata", None)
    usage = ""
    if um:
        usage = f"in {getattr(um, 'prompt_token_count', '?')} / out {getattr(um, 'candidates_token_count', '?')} tok"
    for cand in resp.candidates or []:
        for part in cand.content.parts or []:
            blob = getattr(part, "inline_data", None)
            if blob and blob.data:
                return blob.data, usage
    raise RuntimeError(f"回應裡沒有影像：{getattr(resp, 'text', '')[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="產生整組風格一致的絕技圖示")
    ap.add_argument("--scenario", default="tempest")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--only", action="append")
    ap.add_argument("--anchor", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.scenario not in ICONS and args.scenario != "tempest":
        raise SystemExit("目前只實作 tempest 絕技圖示表")
    if not args.anchor:
        args.anchor = DEFAULT_ANCHOR.get(args.scenario, next(iter(ICONS)))

    out_dir = OUT / args.scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client()

    ids = list(ICONS)
    order = sorted(ids, key=lambda s: (s != args.anchor,))
    anchor_bytes: bytes | None = None
    anchor_path = out_dir / f"{args.anchor}.jpg"
    regenerating_anchor = args.force and (not args.only or args.anchor in args.only)
    if anchor_path.exists() and not regenerating_anchor:
        anchor_bytes = anchor_path.read_bytes()

    made = skipped = failed = 0
    for aid in order:
        if args.only and aid not in args.only:
            continue
        dest = out_dir / f"{aid}.jpg"
        if dest.exists() and not args.force:
            print(f"  已有 {dest.name}，跳過")
            skipped += 1
            if aid == args.anchor:
                anchor_bytes = dest.read_bytes()
            continue
        prompt = f"{STYLE}\n{ICONS[aid]}\n"
        ref = None if aid == args.anchor else anchor_bytes
        if ref is None and aid != args.anchor:
            print(f"  ⚠ {aid}：還沒有錨圖")
        t0 = time.time()
        try:
            data, usage = generate(client, args.model, prompt, ref)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {aid} 失敗：{type(e).__name__}: {e}")
            failed += 1
            continue
        dest.write_bytes(data)
        made += 1
        print(f"  ✓ {aid}  {len(data)/1024:.0f} KB  {time.time()-t0:.1f}s  {usage}")
        if aid == args.anchor:
            anchor_bytes = data

    print(f"\n畫了 {made} 張、跳過 {skipped}、失敗 {failed} → {out_dir}")


if __name__ == "__main__":
    main()
