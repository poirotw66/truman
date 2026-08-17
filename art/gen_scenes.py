"""用 Gemini 影像模型產生劇本場景／關鍵美術，風格必須整組一致。

    python art/gen_scenes.py --scenario tempest
    python art/gen_scenes.py --scenario tempest --force
    python art/gen_scenes.py --scenario tempest --only keyart_tempest

一致性靠：共用 STYLE + 先畫錨圖（keyart）再當參考影像餵給其餘張。
輸出 art/scenes/<scenario>/*.jpg
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
OUT = Path(__file__).with_name("scenes")

STYLE = """\
A dramatic manga-style establishing shot for a Taiwanese coastal fishing village on the eve of a typhoon surge.
Wide cinematic composition, landscape 3:2.

STYLE — apply identically to every image in this set:
- Bold high-contrast inking, dramatic variation in line weight, heavy black shadows with sharp angular edges.
- Hyper-saturated but weather-stained colour: teal sea, slate cloud walls, salt-bleached wood, iron gates,
  temple indigo, wet stone. Metallic sheen on wet surfaces.
- Halftone dots and cross-hatching in shadow areas; thin speed / rain streaks where wind is strong.
- Exactly ONE large stylised Chinese glyph floating as pure graphic lettering (NOT a caption): 「%(onom)s」.
  Render it in BOLD WHITE with a thick dark outline and soft drop shadow, upper-left corner.
- No people as the main subject (tiny distant silhouettes OK). No watermark, no signature, no UI frame.
- Polished premium key-art finish — same illustrated world as the character standees.
"""

ANCHOR_NOTE = """\

STYLE REFERENCE: match the attached reference image EXACTLY — same inking weight, same halftone and
cross-hatch, same saturation and colour grading, same rain/wind treatment, same level of detail.
This is a DIFFERENT location in the SAME illustrated town. Do not copy the reference composition —
only the rendering style.
"""

# stem → (onom, aspect, prompt body). aspect for Gemini ImageConfig.
SCENES: dict[str, dict[str, str]] = {
    "keyart_tempest": dict(
        onom="潮",
        aspect="3:2",
        who="""\
HERO KEY ART — full-bleed coastal vista of 嵐潮鎮 before the surge.
Foreground: wet seawall stones and a half-open iron sluice, spray catching rim light.
Mid: wooden fishing harbour with boats straining on ropes; beyond it a compact village of tin roofs,
brick houses, and a sea-facing temple with a rusted bell tower.
Background: a black-teal cloud wall rolling in from the open ocean, a white tide-line far offshore.
Mood: epic, urgent, beautiful and doomed unless the rite and gate hold. No large characters.""",
    ),
    "keyart_night": dict(
        onom="夜",
        aspect="3:2",
        who="""\
NIGHT AFTERMATH KEY ART of the same Taiwanese coastal village after the storm peak.
Flooded low streets reflecting oil-lamp and temple lantern light; higher ground still dry.
Seawall black with wet; some boats smashed against piles; wind quieter but rain still streaks.
Mood: hush after violence — did the village hold? Atmospheric, sombre, hopeful rim light on the temple.""",
    ),
    "scene_gaodi": dict(
        onom="高",
        aspect="3:2",
        who="""\
LOCATION: 高地 — the northern high rocky ground, the only place the first surge cannot reach.
Narrow path of wet stone leading up between scrub and wind-bent trees; a small shelter roof below.
Looking south you glimpse the village roofs and the angry sea. Evacuation bottleneck energy.""",
    ),
    "scene_cunzhang": dict(
        onom="宅",
        aspect="3:2",
        who="""\
LOCATION: 村長宅 — village head's courtyard house. Wooden doors always ajar, name plaques of past
heads on the hall wall, rain beating the tiled roof, a whistle and ledger on a table by the door.
Practical Taiwanese coastal home, not a palace; urgency without luxury.""",
    ),
    "scene_miao": dict(
        onom="廟",
        aspect="3:2",
        who="""\
LOCATION: 鎮廟 — old sea-facing temple. Thick incense ash, rusted bronze bell, weathered idols,
stone steps wet with rain, doors open toward the black sea. This is where the 海醮 must be seen.
Sacred, cramped, wind tearing prayer flags.""",
    ),
    "scene_guangchang": dict(
        onom="場",
        aspect="3:2",
        who="""\
LOCATION: 廣場 — village square of wet flagstones where nets usually dry.
Tonight: toppled drying racks, scattered baskets, panic footprints in puddles, signs pointing north
to high ground and temple. The emotional centre of the evacuation.""",
    ),
    "scene_tiepu": dict(
        onom="鐵",
        aspect="3:2",
        who="""\
LOCATION: 鐵鋪 — blacksmith shop. Furnace glow small against storm dark, piles of anchor chain and
sluice plates by the door, leather apron on a peg, sparks dying in the wet wind. Work-first grit.""",
    ),
    "scene_yushi": dict(
        onom="市",
        aspect="3:2",
        who="""\
LOCATION: 漁市 — fish market mud lanes already emptying. Salt smell made visible as hanging scales
and empty stalls, tarps whipping, crates abandoned mid-pack. First place to go hollow when tide-talk
tightens.""",
    ),
    "scene_liangcang": dict(
        onom="倉",
        aspect="3:2",
        who="""\
LOCATION: 糧倉 — heavy wooden public granary doors, sacks of millet stacked inside dim light,
rain streaking the outer wall. A place of last stores, not comfort.""",
    ),
    "scene_haidi": dict(
        onom="堤",
        aspect="3:2",
        who="""\
LOCATION: 海堤 — stone seawall mid-section with the iron water gate / sluice.
Foam forcing through seams, spray over the coping, welding tools and plates waiting.
This is where the gate must be sealed before the surge. Violent weather, industrial folk engineering.""",
    ),
    "scene_yugang": dict(
        onom="港",
        aspect="3:2",
        who="""\
LOCATION: 漁港 — wooden pier and moorings. Ropes singing in wind, empty berths for boats still at sea,
signal lantern swinging, white tide-line visible offshore. Rescue-the-fleet urgency.""",
    ),
}

DEFAULT_ANCHOR = {
    "tempest": "keyart_tempest",
}


def generate(client, model: str, prompt: str, aspect: str, ref: bytes | None) -> tuple[bytes, str]:
    parts: list = [prompt + (ANCHOR_NOTE if ref else "")]
    if ref:
        parts.append(types.Part.from_bytes(data=ref, mime_type="image/png"))
    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=aspect),
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
    ap = argparse.ArgumentParser(description="產生整組風格一致的場景／關鍵美術")
    ap.add_argument("--scenario", default="tempest")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--only", action="append", help="只畫這些 stem，可重複")
    ap.add_argument("--anchor", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.scenario not in ("tempest",):
        raise SystemExit("目前只實作 tempest 場景表；江湖場景已有成圖")
    if not args.anchor:
        args.anchor = DEFAULT_ANCHOR[args.scenario]

    out_dir = OUT / args.scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client()

    stems = list(SCENES)
    order = sorted(stems, key=lambda s: (s != args.anchor,))
    anchor_bytes: bytes | None = None
    anchor_path = out_dir / f"{args.anchor}.jpg"
    regenerating_anchor = args.force and (not args.only or args.anchor in args.only)
    if anchor_path.exists() and not regenerating_anchor:
        anchor_bytes = anchor_path.read_bytes()

    made = skipped = failed = 0
    for stem in order:
        if args.only and stem not in args.only:
            continue
        dest = out_dir / f"{stem}.jpg"
        if dest.exists() and not args.force:
            print(f"  已有 {dest.name}，跳過（--force 可重畫）")
            skipped += 1
            if stem == args.anchor:
                anchor_bytes = dest.read_bytes()
            continue

        spec = SCENES[stem]
        prompt = (
            f"{STYLE % {'onom': spec['onom']}}\n"
            f"SHOT — {stem}:\n{spec['who']}\n"
        )
        ref = None if stem == args.anchor else anchor_bytes
        if ref is None and stem != args.anchor:
            print(f"  ⚠ {stem}：還沒有錨圖，這張會少了風格對齊")
        t0 = time.time()
        try:
            data, usage = generate(client, args.model, prompt, spec["aspect"], ref)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {stem} 失敗：{type(e).__name__}: {e}")
            failed += 1
            continue
        dest.write_bytes(data)
        made += 1
        print(f"  ✓ {stem}  {len(data)/1024:.0f} KB  {time.time()-t0:.1f}s  {usage}")
        if stem == args.anchor:
            anchor_bytes = data

    print(f"\n畫了 {made} 張、跳過 {skipped}、失敗 {failed} → {out_dir}")


if __name__ == "__main__":
    main()
