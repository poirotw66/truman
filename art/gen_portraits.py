"""用 Gemini 影像模型產生人物立繪，風格必須整組一致。

    python art/gen_portraits.py                     # 全部（已存在的跳過）
    python art/gen_portraits.py --force             # 全部重畫
    python art/gen_portraits.py --only fei_bin      # 只畫一個
    python art/gen_portraits.py --anchor liu_zhengfeng

一致性靠兩件事，缺一不可：
  1. 所有 prompt 共用同一段 STYLE，一個字都不改；
  2. 先畫「風格錨」那一張，之後每一張都把錨圖當參考影像餵進去，
     明確要求對齊線條、上色、網點與背景處理。單靠文字描述不可能對齊。

會花錢（每張一次 API 呼叫）。人物資料讀 `cast/<劇本>.default.json`，
所以在人物工作室改了誰的衣服顏色，重跑這支就會跟著變。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

try:  # Windows 主控台預設 cp950，印不出 ✓ 之類的字
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

MODEL = "gemini-3.1-flash-lite-image"
OUT = Path(__file__).with_name("portraits")

# ---------------------------------------------------------------- 風格
# 這一段是整組的共同語言。任何一個字改了，就得整組重畫，否則風格會裂開。
# SUBJECT WORLD 依劇本換（武俠／漁村），其餘線條與上色規則必須同一套。
STYLE = """\
A dramatic manga-style character portrait. Bust / upper-body composition, vertical 3:4.

STYLE — apply identically to every image in this set:
- Bold high-contrast inking, dramatic variation in line weight, heavy black shadows with sharp angular edges.
- Hyper-saturated, unconventional colour choices; clashing complementary accents; a metallic sheen on fabric.
- Extremely fashion-forward costume design: exaggerated ornament, layered fabric, theatrical drape, cloth caught mid-motion.
- A theatrical twisted contrapposto pose, spine coiled like a spring, fingers splayed in an elegant deliberate gesture.
- Halftone dots and cross-hatching in the FABRIC shadows; speed lines radiating behind the figure.
- Exactly ONE large stylised Chinese glyph floating behind the figure as a pure graphic element (sound-effect
  lettering, NOT a caption): 「%(onom)s」. Render it in BOLD WHITE with a thick dark outline and a soft drop
  shadow, placed in the upper-left corner. Never solid black, never a plain caption.
- Flat two-tone background built on %(bg)s: a smooth vertical gradient from a lighter to a deeper shade of
  that colour, plus a radial burst of thin rays behind the figure.

BEAUTY — this is a cast of beautiful people; every face must be genuinely attractive:
- Idealised leading-role good looks. Flawless luminous skin rendered with SMOOTH GRADIENTS (not flat fill),
  elegant long neck, immaculate proportions, graceful hands with long fingers.
- Men: handsome and charismatic — chiselled but refined bone structure, strong jaw, high cheekbones,
  deep-set intense eyes, straight nose, broad shoulders, effortless confidence.
- Women: beautiful and graceful — large luminous eyes with long lashes, soft refined features, delicate
  poise, serene dignity. Depict with complete modesty: covering clothes, no sexualised posing or framing.
- Eyes rendered in high detail: coloured irises with visible gradient, crisp catchlights, defined lashes.
- Hair with glossy specular highlights and individually inked strands catching the light.
- Cinematic rim light tracing the whole silhouette, separating the figure from the background.
- Polished, high-detail illustration finish — this should look like premium key art.

%(world)s
No lettering other than the single glyph. No watermark, no signature, no frame, no border.
"""

WORLDS = {
    "jianghu": (
        "SUBJECT WORLD: Ming-dynasty Chinese wuxia (martial arts) — hanfu robes, wide sleeves, "
        "sashes, topknots. No modern clothing."
    ),
    "tempest": (
        "SUBJECT WORLD: Taiwan coastal fishing village on the eve of a typhoon surge — temple keepers, "
        "blacksmiths, fishers, herbalists, and watchmen. Practical coastal workwear and folk temple dress "
        "(indigo vests, oilskin, leather aprons, rain cloaks, prayer beads). Not ancient hanfu wuxia; "
        "not glossy modern city fashion. Wind and salt in the hair; storm urgency in every gesture."
    ),
}

# 每個人的姿態、表情、道具。人設決定姿態——這是 JoJo 立最重要的地方：
# 姿勢要洩漏這個人今天想幹什麼。
POSE = {
    "liu_zhengfeng": dict(
        onom="鏘", who="""\
A strikingly handsome swordmaster in his late forties — silver at the temples, a neatly groomed short beard
framing a strong jaw, calm deep-set eyes with fine expression lines. Aristocratic bearing.
Deep jade-green layered silk robe with dark teal trim and a gold-threaded sash; a jade pendant swings at his belt.
POSE: standing tall, torso twisted, one arm swept across his chest in a formal farewell salute, the other hand
open and lowered above a golden washing basin, sleeve billowing upward. EXPRESSION: serene, noble, resolute,
with grief held tight behind the eyes — a man publicly giving something up.""",
    ),
    "fei_bin": dict(
        onom="轟", who="""\
A coldly handsome martial enforcer in his thirties — sharp angular features, immaculate clean-shaven jaw,
narrow piercing eyes, not a hair out of place. Severe, elegant, dangerous. Black winged official's cap.
Slate blue-grey robe, near-black, with gold-embroidered collar and stiff high neck.
POSE: leaning forward from the hips, chin lowered, one gloved hand raised with index finger extended in
accusation, the other resting on a sheathed sword. EXPRESSION: cold, righteous, contemptuous — a beautiful
face wearing an ugly certainty.""",
    ),
    "linghu_chong": dict(
        onom="嘩", who="""\
A very handsome young swordsman in his early twenties — bright warm eyes, an easy crooked grin, tousled
topknot with loose strands falling across his brow, lean athletic build. Roguish and magnetic.
Amber-gold robe worn carelessly open at the collar, sash tied loose; a wine gourd hangs at his hip.
POSE: weight thrown onto one leg, hip cocked, one arm flung wide holding a wine gourd aloft, the other hand
resting on a sword hilt behind him. EXPRESSION: reckless, laughing, warm — completely unbothered by danger.""",
    ),
    "yi_lin": dict(
        onom="顫", who="""A beautiful young CHINESE BUDDHIST novice nun, sixteen — delicate luminous features, very large clear eyes
with long lashes brimming with tears, a small soft mouth, smooth pale skin. Serene and innocent; her beauty
is fragile and gentle, never sensual.
IMPORTANT — she is a Chinese Buddhist nun, NOT a Western or Christian nun. Absolutely no white wimple, coif,
veil or habit. Her head is shaven, bare or under a simple round grey Chinese monastic cap; the robe is a
loose grey-brown Chinese 海青 with wide sleeves and a crossed collar, an ochre kasaya sash over the left
shoulder, wooden prayer beads at her wrist.
MODESTY IS MANDATORY: robes cover her completely to the throat and wrists. Straightforward bust framing —
nothing suggestive in pose, camera angle or clothing.
POSE: shoulders drawn in, body half-turned away as if to flee, both hands clasped tight at her chest around
the beads, fingers trembling. EXPRESSION: frightened, pleading, eyes wide and wet — a child who has
understood she is somewhere she cannot survive.""",
    ),
    "tian_boguang": dict(
        onom="颯", who="""\
A dangerously handsome bandit swordsman in his early thirties — lean hard face, stubbled jaw, a thin scar
down one cheek that only makes him more striking, wind-blown hair loose beneath a gold headband, a wolfish
grin showing a hint of teeth. Predatory charisma.
Dark crimson robe with the collar torn open, black under-robe beneath, muscular forearms bare.
POSE: crouched low and coiled sideways, one hand reversed on the hilt of a curved sabre mid-draw, the other
hand splayed forward. EXPRESSION: amused, half-lidded, hungry.""",
    ),
    "qu_yang": dict(
        onom="錚", who="""\
A strikingly distinguished old man — long flowing silver-white hair and beard, a fine aquiline nose,
deep-set gentle eyes, elegant gaunt cheekbones; the beauty of great age carried well. Refined and serene.
Deep violet-purple silk robe with black trim and wide sleeves; a long lacquered guqin under one arm.
POSE: half-turned away, looking back over his shoulder, one hand raised with fingers curled as though he has
just plucked a string that is still ringing. EXPRESSION: gentle, sorrowful, unafraid — an old killer who has
stopped caring about anything except one friendship.""",
    ),
    # --- tempest · 嵐潮鎮 ---
    "shen_xi": dict(
        onom="鐘", who="""\
A handsome Taiwanese temple keeper in his mid-fifties — salt-and-pepper hair cropped short, a neat short
beard, deep calm eyes with weathered crow's feet, strong still hands. Quiet gravity; everyone calls him
金水伯.
Deep indigo temple vest over a dark teal under-robe, wooden prayer beads at the wrist, incense smoke
curling past his shoulder.
POSE: standing firm as if before an altar, torso slightly twisted, one hand raising a small temple bell,
the other open in invitation toward unseen worshippers. EXPRESSION: resolute, devout, urgent — a man who
knows an empty temple does not count.""",
    ),
    "shi_lei": dict(
        onom="砰", who="""\
A powerfully built handsome Taiwanese blacksmith around forty — square jaw with short stubble, thick
forearms, soot-smudged cheekbones, intense narrow eyes. Sparse with words; the village calls him 鐵雄.
Heavy leather apron over a burnt-umber work shirt, dark iron-grey trim; a welding hammer or tongs in hand,
ember highlights on metal.
POSE: weight forward, one knee slightly bent as if bracing a gate, hammer raised mid-strike, other hand
steadying an unseen sluice plate. EXPRESSION: grim focus — prayer will not hold a gate; steel will.""",
    ),
    "fang_lan": dict(
        onom="令", who="""\
A beautiful, commanding Taiwanese village headwoman of thirty-eight — clear sharp eyes, neat dark hair
pulled back for work, strong graceful posture. Practical authority; everyone calls her 美華姐.
MODESTY IS MANDATORY: high collar, full sleeves. Sea-green practical jacket over a darker teal blouse,
ink-dark trim; a cloth tally cord or whistle at her belt — no frills.
POSE: half-turned toward the viewer, one arm flung out pointing inland (toward high ground), the other
hand open as if counting people to move. EXPRESSION: firm, urgent, compassionate without softness —
she cannot argue with the sea, only with people.""",
    ),
    "a_qian": dict(
        onom="潮", who="""\
A very handsome Taiwanese fisherman of twenty-nine — sun-browned skin, bright keen eyes that read the
tide, wind-tossed dark hair under a simple cloth headband, lean athletic build. The village just calls
him 阿海.
Deep sea-blue oilskin jacket half-open at the collar, darker teal undershirt, a coiled rope over one
shoulder; salt spray catching the rim light.
POSE: mid-stride as if racing back to harbour, one arm raised signaling boats offshore, the other gripping
the rope. EXPRESSION: urgent grin, alive to danger — ships still at sea means bodies the rite cannot save.""",
    ),
    "qing_he": dict(
        onom="安", who="""\
A beautiful Taiwanese herbal healer of thirty-three — warm luminous eyes, gentle but unflinching mouth,
dark hair tied simply, soft sun-weathered skin. The village comes to her for medicine and calm.
MODESTY IS MANDATORY: covering clothes to the throat and wrists. Soft sage-and-sand herbalist jacket with
moss-green trim; a cloth medicine pouch at her side; a sprig of dried herbs tucked near the collar.
POSE: body angled as if guiding someone uphill, one hand outstretched to steady a panicked neighbour,
the other clutching the pouch. EXPRESSION: steady compassion — she knows panic kills before the tide does.""",
    ),
    "gu_chao": dict(
        onom="望", who="""\
A weathered handsome Taiwanese dyke watchman of forty-five — lean face, stubbled jaw, sharp far-seeing
eyes, wind-cut hair under a faded cloth band. Everyone just calls him 阿德.
Storm-grey canvas coat over charcoal work layers, salt-stained trim; one hand shielding his eyes toward
the sea as if reading cloud walls.
POSE: crouched slightly on an unseen seawall, torso twisted looking back inland while pointing seaward
with the other hand — calling for the blacksmith. EXPRESSION: taut alertness — he cannot weld the gate,
but he can outrun the wind to fetch the man who can.""",
    ),
}

DEFAULT_ANCHOR = {
    "jianghu": "liu_zhengfeng",
    "tempest": "shen_xi",
}

ANCHOR_NOTE = """\

STYLE REFERENCE: match the attached reference image EXACTLY — same inking weight, same halftone and cross-hatch
technique, same saturation and colour grading, same background treatment, same level of detail and finish.
This is a different character in the SAME illustrated set. Do not copy the reference character's face,
costume, colours or pose — only the rendering style.
"""


def load_cast(scenario: str) -> list[dict]:
    p = ROOT / "cast" / f"{scenario}.default.json"
    if not p.exists():
        raise SystemExit(f"找不到 {p}，先跑 python cast/build_editor.py {scenario}")
    return json.loads(p.read_text(encoding="utf-8"))["agents"]


def build_prompt(agent: dict, pose: dict, scenario: str = "jianghu") -> str:
    # cast 現在用 look；舊檔可能還叫 art
    look = agent.get("look") or agent.get("art") or {}
    bg = look.get("color") or "#8a8272"
    world = WORLDS.get(scenario) or WORLDS["jianghu"]
    head = STYLE % {"onom": pose["onom"], "bg": bg, "world": world}
    return (
        f"{head}\n"
        f"CHARACTER — {agent['name']} ({look.get('sect','')} · {look.get('title','')}):\n"
        f"{pose['who']}\n"
        f"Costume palette must be built on {look.get('robe','#6f6455')} with "
        f"{look.get('trim','#463f34')} trim, pushed to high saturation.\n"
    )


def generate(client, model: str, prompt: str, ref: bytes | None) -> tuple[bytes, str]:
    parts: list = [prompt + (ANCHOR_NOTE if ref else "")]
    if ref:
        parts.append(types.Part.from_bytes(data=ref, mime_type="image/png"))
    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio="3:4"),
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
    ap = argparse.ArgumentParser(description="產生整組風格一致的人物立繪")
    ap.add_argument("--scenario", default="jianghu")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--only", action="append", help="只畫這些 id，可重複")
    ap.add_argument("--anchor", default=None, help="當風格錨的角色 id（預設依劇本）")
    ap.add_argument("--force", action="store_true", help="已存在也重畫")
    args = ap.parse_args()
    if not args.anchor:
        args.anchor = DEFAULT_ANCHOR.get(args.scenario, "liu_zhengfeng")

    agents = load_cast(args.scenario)
    out_dir = OUT / args.scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client()

    # 錨圖排第一個，後面的人才有東西可以對齊
    order = sorted(agents, key=lambda a: (a["id"] != args.anchor,))
    anchor_bytes: bytes | None = None
    anchor_path = out_dir / f"{args.anchor}.png"
    if anchor_path.exists() and not args.force:
        anchor_bytes = anchor_path.read_bytes()

    made = skipped = failed = 0
    for a in order:
        aid = a["id"]
        if aid not in POSE:
            print(f"  跳過 {aid}：POSE 裡沒有這個人的姿態設定")
            skipped += 1
            continue
        if args.only and aid not in args.only:
            continue
        dest = out_dir / f"{aid}.png"
        if dest.exists() and not args.force:
            print(f"  已有 {dest.name}，跳過（--force 可重畫）")
            skipped += 1
            if aid == args.anchor:
                anchor_bytes = dest.read_bytes()
            continue

        ref = None if aid == args.anchor else anchor_bytes
        if ref is None and aid != args.anchor:
            print(f"  ⚠ {aid}：還沒有錨圖，這張會少了風格對齊")
        prompt = build_prompt(a, POSE[aid], args.scenario)
        t0 = time.time()
        try:
            data, usage = generate(client, args.model, prompt, ref)
        except Exception as e:  # noqa: BLE001 - 一張失敗不該讓整組停下來
            print(f"  ✗ {a['name']}（{aid}）失敗：{type(e).__name__}: {e}")
            failed += 1
            continue
        dest.write_bytes(data)
        made += 1
        print(f"  ✓ {a['name']}（{aid}）{len(data)/1024:.0f} KB  {time.time()-t0:.1f}s  {usage}")
        if aid == args.anchor:
            anchor_bytes = data

    print(f"\n畫了 {made} 張、跳過 {skipped}、失敗 {failed} → {out_dir}")
    if made:
        print(f"接著跑：python art/embed_portraits.py {args.scenario}，確認內嵌體積")


if __name__ == "__main__":
    main()
