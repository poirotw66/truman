"""用 Gemini 影像模型產生劇本場景／關鍵美術，風格必須整組一致。

    python art/gen_scenes.py --scenario tempest
    python art/gen_scenes.py --scenario jianghu --force
    python art/gen_scenes.py --scenario jianghu --only keyart_hengshan

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

NO_TEXT = """\
- CRITICAL — ZERO written characters anywhere: no Chinese/Japanese glyphs, no letters, no numbers-as-labels,
  no captions, no sound-effect lettering, no watermarks, no signatures, no UI frame. Pure illustration only.
"""

STYLES = {
    "tempest": f"""\
A dramatic manga-style establishing shot for a Taiwanese coastal fishing village on the eve of a typhoon surge.
Wide cinematic composition, landscape 3:2.

STYLE — apply identically to every image in this set:
- Bold high-contrast inking, dramatic variation in line weight, heavy black shadows with sharp angular edges.
- Hyper-saturated but weather-stained colour: teal sea, slate cloud walls, salt-bleached wood, iron gates,
  temple indigo, wet stone. Metallic sheen on wet surfaces.
- Halftone dots and cross-hatching in shadow areas; thin speed / rain streaks where wind is strong.
- No people as the main subject (tiny distant silhouettes OK).
{NO_TEXT}\
- Polished premium key-art finish — same illustrated world as the character standees.
""",
    "jianghu": f"""\
A dramatic manga-style establishing shot for a Ming-dynasty Chinese wuxia mountain city (Hengshan).
Wide cinematic composition, landscape 3:2.

STYLE — apply identically to every image in this set:
- Bold high-contrast inking, dramatic variation in line weight, heavy black shadows with sharp angular edges.
- Hyper-saturated colour: emerald tiled roofs, crimson pillars and carpets, gold ritual metal, teal mountain mist,
  amber sunset rim light. Metallic sheen on bronze and wet stone.
- Halftone dots and cross-hatching in shadow areas; thin speed / sun-ray lines where light is dramatic.
- No people as the main subject (tiny distant silhouettes OK). Ming-dynasty architecture only — no modern objects.
{NO_TEXT}\
- Polished premium key-art finish — same illustrated world as the character standees.
""",
}

ANCHOR_NOTE = """\

STYLE REFERENCE: match the attached reference image EXACTLY — same inking weight, same halftone and
cross-hatch, same saturation and colour grading, same weather/light treatment, same level of detail.
This is a DIFFERENT location in the SAME illustrated world. Do not copy the reference composition —
only the rendering style.
"""

# scenario → stem → {aspect, who}
SCENES: dict[str, dict[str, dict[str, str]]] = {
    "tempest": {
        "keyart_tempest": dict(
            aspect="3:2",
            who="""\
HERO KEY ART — full-bleed coastal vista of the fishing town before the surge.
Foreground: wet seawall stones and a half-open iron sluice, spray catching rim light.
Mid: wooden fishing harbour with boats straining on ropes; beyond it a compact village of tin roofs,
brick houses, and a sea-facing temple with a rusted bell tower.
Background: a black-teal cloud wall rolling in from the open ocean, a white tide-line far offshore.
Mood: epic, urgent, beautiful and doomed unless the rite and gate hold. No large characters.""",
        ),
        "keyart_night": dict(
            aspect="3:2",
            who="""\
NIGHT AFTERMATH KEY ART of the same Taiwanese coastal village after the storm peak.
Flooded low streets reflecting oil-lamp and temple lantern light; higher ground still dry.
Seawall black with wet; some boats smashed against piles; wind quieter but rain still streaks.
Mood: hush after violence — did the village hold? Atmospheric, sombre, hopeful rim light on the temple.""",
        ),
        "scene_gaodi": dict(
            aspect="3:2",
            who="""\
LOCATION: northern high rocky ground — the only place the first surge cannot reach.
Narrow path of wet stone leading up between scrub and wind-bent trees; a small shelter roof below.
Looking south you glimpse the village roofs and the angry sea. Evacuation bottleneck energy.""",
        ),
        "scene_cunzhang": dict(
            aspect="3:2",
            who="""\
LOCATION: village head's courtyard house. Wooden doors always ajar, weathered wooden plaques of past
heads on the hall wall (illegible carved marks only), rain beating the tiled roof,
a whistle and blank ledger on a table by the door.
Practical Taiwanese coastal home, not a palace; urgency without luxury.""",
        ),
        "scene_miao": dict(
            aspect="3:2",
            who="""\
LOCATION: old sea-facing temple. Thick incense ash, rusted bronze bell, weathered idols,
stone steps wet with rain, doors open toward the black sea. This is where the sea rite must be seen.
Sacred, cramped, wind tearing cloth prayer flags (plain dyed cloth — no readable writing on flags).""",
        ),
        "scene_guangchang": dict(
            aspect="3:2",
            who="""\
LOCATION: village square of wet flagstones where nets usually dry.
Tonight: toppled drying racks, scattered baskets, panic footprints in puddles, plain wooden directional
markers pointing north to high ground and temple (shapes/arrows only).
The emotional centre of the evacuation.""",
        ),
        "scene_tiepu": dict(
            aspect="3:2",
            who="""\
LOCATION: blacksmith shop. Furnace glow small against storm dark, piles of anchor chain and
sluice plates by the door, leather apron on a peg, sparks dying in the wet wind. Work-first grit.""",
        ),
        "scene_yushi": dict(
            aspect="3:2",
            who="""\
LOCATION: fish market mud lanes already emptying. Salt smell made visible as hanging scales
and empty stalls, tarps whipping, crates abandoned mid-pack. First place to go hollow when tide-talk
tightens.""",
        ),
        "scene_liangcang": dict(
            aspect="3:2",
            who="""\
LOCATION: heavy wooden public granary doors, sacks of millet stacked inside dim light,
rain streaking the outer wall. A place of last stores, not comfort.""",
        ),
        "scene_haidi": dict(
            aspect="3:2",
            who="""\
LOCATION: stone seawall mid-section with the iron water gate / sluice.
Foam forcing through seams, spray over the coping, welding tools and plates waiting.
This is where the gate must be sealed before the surge. Violent weather, industrial folk engineering.""",
        ),
        "scene_yugang": dict(
            aspect="3:2",
            who="""\
LOCATION: wooden pier and moorings. Ropes singing in wind, empty berths for boats still at sea,
signal lantern swinging, white tide-line visible offshore. Rescue-the-fleet urgency.""",
        ),
    },
    "jianghu": {
        "keyart_hengshan": dict(
            aspect="3:2",
            who="""\
HERO KEY ART — full-bleed vista of Hengshan wuxia city carved into jagged green mountains at sunset.
Foreground: a stone balcony with railing and a large ornate golden washing basin on a crimson carpet
(ritual basin for a farewell ceremony) — no readable carving on the metal.
Mid: tiered fortress walls, emerald-green tiled roofs, crimson pillars, gate towers climbing the ridge.
Background: sharp peaks in teal mist, sun low with dramatic radial light rays.
Mood: epic, ceremonial, beautiful and tense — a day of washing hands that will not stay peaceful.
No large characters.""",
        ),
        "keyart_night": dict(
            aspect="3:2",
            who="""\
NIGHT AFTERMATH KEY ART of the same Hengshan mountain city after the day's violence.
Oil-lamp and lantern light on wet stone streets; some courtyard doors left open; distant roof ridges dark.
Mood: hush after swords — who lived, who left? Atmospheric, sombre, residual gold rim light on temple eaves.""",
        ),
        "scene_liufu": dict(
            aspect="3:2",
            who="""\
LOCATION: Liu manor main hall. Red carpet on the floor, a golden washing basin set for a formal farewell
rite, wide wooden doors open to the courtyard, hanging silk banners that are plain dyed cloth only
(no readable writing). Elegant Ming-dynasty martial household, ceremonial and tense.""",
        ),
        "scene_market": dict(
            aspect="3:2",
            who="""\
LOCATION: crowded street market. Rice sacks, cloth bolts, knife-sheath stalls in a noisy row;
awning shadows, steam from food stalls, people as tiny silhouettes only. Bustling Hengshan commerce.""",
        ),
        "scene_tavern": dict(
            aspect="3:2",
            who="""\
LOCATION: three-storey wine house / tavern — the tallest in the city. Wooden balconies looking over half
the town, wine jars and empty cups on tables, dusk light through lattice windows. High vantage, social hub.""",
        ),
        "scene_plaza": dict(
            aspect="3:2",
            who="""\
LOCATION: martial practice ground before the manor. Blue-grey flagstones, rope barriers for spectators,
weapon racks at the edge, open sky. Where crowds gather to watch what happens today.""",
        ),
        "scene_gate": dict(
            aspect="3:2",
            who="""\
LOCATION: eastern city gate. Massive timber doors half-open, stone arch, lazy guards as tiny silhouettes,
the road out of the city stretching toward misty hills. Escape route energy.""",
        ),
        "scene_yard": dict(
            aspect="3:2",
            who="""\
LOCATION: manor rear courtyard. Firewood stacks, empty wine jars, a side door to the long street,
mossy stone and laundry poles. Quiet backstage of the household.""",
        ),
        "scene_qunyu": dict(
            aspect="3:2",
            who="""\
LOCATION: southern courtyard house by day — few people, two red lanterns at the gate (plain lanterns,
no writing), whitewashed walls, quiet elegance with a hint of secrecy.""",
        ),
        "scene_temple": dict(
            aspect="3:2",
            who="""\
LOCATION: small neglected city-god shrine. Thin incense, a clay idol missing half a hand, covered corridor
good for sheltering from rain. Worn sacred space, not grand.""",
        ),
        "scene_shrine": dict(
            aspect="3:2",
            who="""\
LOCATION: ruined shrine outside the city. Collapsed offering table, broken roof beams, weeds in the
courtyard stones. A place travellers briefly rest — lonely, wind-scraped.""",
        ),
    },
}

DEFAULT_ANCHOR = {
    "tempest": "keyart_tempest",
    "jianghu": "keyart_hengshan",
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
    ap.add_argument("--scenario", default="tempest", choices=sorted(SCENES))
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--only", action="append", help="只畫這些 stem，可重複")
    ap.add_argument("--anchor", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if not args.anchor:
        args.anchor = DEFAULT_ANCHOR[args.scenario]

    style = STYLES[args.scenario]
    table = SCENES[args.scenario]
    out_dir = OUT / args.scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client()

    stems = list(table)
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

        spec = table[stem]
        prompt = f"{style}\nSHOT — {stem}:\n{spec['who']}\n"
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
