"""用 Gemini 影像模型產生事件流小圖示（1:1），風格必須整組一致、圖上不要有字。

    python art/gen_event_icons.py
    python art/gen_event_icons.py --force
    python art/gen_event_icons.py --only speech

輸出 art/icons/events/<key>.jpg
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
OUT = Path(__file__).with_name("icons") / "events"

STYLE = """\
A premium game event-feed icon, square 1:1 composition, centred subject filling most of the frame.

STYLE — apply identically to every icon in this set:
- Bold comic / manga inking with clear silhouette readable at 48px and at 24px.
- Rich saturated colour, soft rim light, subtle halftone in shadows.
- Dark smoky background; thin ornamental frame in muted brass / wet-iron with small corner studs.
- Single clear symbolic object or gesture — no people as full scenes.
- CRITICAL — ZERO written characters anywhere: no Chinese/Japanese glyphs, no letters, no numbers-as-labels,
  no captions, no speech-bubble lettering, no blank parchment meant for writing, no watermarks, no signatures.
  Pure illustration only. Do not leave empty cream panels that look like text placeholders.
"""

ANCHOR_NOTE = """\

STYLE REFERENCE: match the attached reference icon EXACTLY — same line weight, framing, lighting,
and finish. Different event, same icon set. Do not copy the reference object — only the rendering style.
"""

# keys match art.embed_event_icons.KEYS
EVENTS: dict[str, str] = {
    "speech": """\
EVENT ICON — dialogue / speech.
Two overlapping comic speech-bubble outlines as pure shapes (empty interiors OK as graphic shapes),
plus a small open mouth silhouette or sound-wave arcs — NO letters inside the bubbles.""",
    "attack": """\
EVENT ICON — combat / attack.
Two crossed curved swords with gold guards, indigo grips, bright silver blades;
crimson energy splash behind the cross point.""",
    "death": """\
EVENT ICON — death.
Weathered stone memorial tablet on a short pedestal, one corner chipped;
a single red brushstroke circle (abstract mark, not a character) on the stone;
deep crimson brush streaks in the dark background.""",
    "reflection": """\
EVENT ICON — sudden insight / reflection.
Bright four-pointed golden starburst with radiating sharp shards on pure black.""",
    "world": """\
EVENT ICON — world / narrator beat.
A compact illustrated coastal or town diorama inside an open ornate frame —
tiny roofs, sea, and cloud — NOT a blank scroll and NOT a writing surface.""",
    "interact": """\
EVENT ICON — interaction / touch.
A hand index finger touching a glowing amber octagonal gem in a dark metal setting;
bright contact spark.""",
    "goal": """\
EVENT ICON — goal achieved.
Thick jade-green checkmark on a dark field with thin gold radial rays;
ornamental brass frame with corner studs.""",
    "goalx": """\
EVENT ICON — goal failed.
Thick crimson X on a cracked dark-red stone slab; ornate dark-gold frame.""",
}

DEFAULT_ANCHOR = "attack"


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
    ap = argparse.ArgumentParser(description="產生整組風格一致的事件流小圖示")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--only", action="append")
    ap.add_argument("--anchor", default=DEFAULT_ANCHOR)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    client = genai.Client()

    ids = list(EVENTS)
    order = sorted(ids, key=lambda s: (s != args.anchor,))
    anchor_bytes: bytes | None = None
    anchor_path = OUT / f"{args.anchor}.jpg"
    regenerating_anchor = args.force and (not args.only or args.anchor in args.only)
    if anchor_path.exists() and not regenerating_anchor:
        anchor_bytes = anchor_path.read_bytes()

    made = skipped = failed = 0
    for key in order:
        if args.only and key not in args.only:
            continue
        dest = OUT / f"{key}.jpg"
        if dest.exists() and not args.force:
            print(f"  已有 {dest.name}，跳過")
            skipped += 1
            if key == args.anchor:
                anchor_bytes = dest.read_bytes()
            continue
        prompt = f"{STYLE}\n{EVENTS[key]}\n"
        ref = None if key == args.anchor else anchor_bytes
        if ref is None and key != args.anchor:
            print(f"  ⚠ {key}：還沒有錨圖")
        t0 = time.time()
        try:
            data, usage = generate(client, args.model, prompt, ref)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {key} 失敗：{type(e).__name__}: {e}")
            failed += 1
            continue
        dest.write_bytes(data)
        made += 1
        print(f"  ✓ {key}  {len(data)/1024:.0f} KB  {time.time()-t0:.1f}s  {usage}")
        if key == args.anchor:
            anchor_bytes = data

    print(f"\n畫了 {made} 張、跳過 {skipped}、失敗 {failed} → {OUT}")


if __name__ == "__main__":
    main()
