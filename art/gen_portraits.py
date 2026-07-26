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
STYLE = """\
A dramatic manga-style character portrait. Bust / upper-body composition, vertical 3:4.

STYLE — apply identically to every image in this set:
- Bold high-contrast inking, dramatic variation in line weight, heavy black shadows with sharp angular edges.
- Hyper-saturated, unconventional colour choices; clashing complementary accents; a metallic sheen on fabric.
- Extremely fashion-forward costume design: exaggerated ornament, layered fabric, theatrical drape, cloth caught mid-motion.
- Hard chiselled facial structure: strong square jaw, deep brow ridge, sharp cheekbones, intense narrowed eyes, corded neck and shoulder muscle.
- A theatrical twisted contrapposto pose, spine coiled like a spring, fingers splayed in an elegant deliberate gesture.
- Halftone dots and cross-hatching for shading; radiating speed lines behind the figure.
- Exactly ONE large stylised Chinese glyph floating behind the figure as a pure graphic element (sound-effect lettering, NOT a caption): 「%(onom)s」.
- Flat two-tone background built on %(bg)s with a subtle radial burst.

SUBJECT WORLD: Ming-dynasty Chinese wuxia (martial arts) — hanfu robes, wide sleeves, sashes, topknots.
No modern clothing. No lettering other than the single glyph. No watermark, no signature, no frame, no border.
"""

# 每個人的姿態、表情、道具。人設決定姿態——這是 JoJo 立最重要的地方：
# 姿勢要洩漏這個人今天想幹什麼。
POSE = {
    "liu_zhengfeng": dict(
        onom="鏘", who="""\
A dignified swordmaster in his fifties. Neat short black beard, hair bound in a topknot pierced by a jade pin.
Deep jade-green layered robe with dark teal trim and a gold-threaded sash; a jade pendant swings from his belt.
POSE: standing tall, torso twisted, one arm swept across his chest in a formal farewell salute, the other hand
open and lowered above a golden washing basin, sleeve billowing upward. EXPRESSION: serene, resolute, with grief
held tight behind the eyes — a man publicly giving something up.""",
    ),
    "fei_bin": dict(
        onom="轟", who="""\
A powerful official-looking martial enforcer. Clean-shaven, severe. Black winged official's cap.
Slate blue-grey robe, near-black, with a gold-embroidered belt and stiff high collar.
POSE: leaning forward from the hips, chin lowered, one gloved hand raised with index finger extended in accusation,
the other resting on a sheathed sword. EXPRESSION: cold, righteous, contemptuous — the face of a man who has already
decided and is only waiting for the right moment.""",
    ),
    "linghu_chong": dict(
        onom="嘩", who="""\
A young swordsman in his twenties, messy topknot with loose strands, no beard, easy crooked grin.
Amber-gold robe worn carelessly open at the collar, sash tied loose; a wine gourd hangs at his hip.
POSE: weight thrown onto one leg, hip cocked, one arm flung wide holding a wine gourd aloft, the other hand
resting on a sword hilt behind him. EXPRESSION: reckless, laughing, warm — completely unbothered by the danger
he is walking into.""",
    ),
    "yi_lin": dict(
        onom="顫", who="""\
A very young Buddhist nun, sixteen years old, shaven-headed under a pale monastic hood, no makeup, delicate features.
Off-white grey monastic robe with an ochre kasaya sash across one shoulder, prayer beads wound around one wrist.
POSE: shoulders drawn in, body half-turned away as if to flee, both hands clasped tight at her chest around the beads,
fingers trembling and splayed. EXPRESSION: frightened, pleading, eyes wide and wet — a child who has understood
she is somewhere she cannot survive.""",
    ),
    "tian_boguang": dict(
        onom="颯", who="""\
A lean, dangerous bandit swordsman, early thirties. Stubbled jaw, a thin scar down one cheek, a gold headband
across his brow, hair loose and wind-blown. Dark crimson robe with the collar torn open, black under-robe beneath.
POSE: crouched low and coiled sideways, one hand reversed on the hilt of a curved sabre mid-draw, the other hand
splayed forward, fingers spread. EXPRESSION: a wolfish predatory smirk, eyes half-lidded and amused.""",
    ),
    "qu_yang": dict(
        onom="錚", who="""\
An old man with a long flowing white beard and long white hair, deep lines carved around the eyes, gaunt and elegant.
Deep violet-purple robe with black trim, wide sleeves; he carries a long lacquered guqin (seven-string zither)
under one arm. POSE: half-turned away, looking back over his shoulder, one hand raised with fingers curled as
though he has just plucked a string that is still ringing. EXPRESSION: gentle, sorrowful, unafraid — an old killer
who has stopped caring about anything except one friendship.""",
    ),
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


def build_prompt(agent: dict, pose: dict) -> str:
    art = agent.get("art", {})
    bg = art.get("color") or "#8a8272"
    head = STYLE % {"onom": pose["onom"], "bg": bg}
    return (
        f"{head}\n"
        f"CHARACTER — {agent['name']} ({art.get('sect','')} · {art.get('title','')}):\n"
        f"{pose['who']}\n"
        f"Costume palette must be built on {art.get('robe','#6f6455')} with "
        f"{art.get('trim','#463f34')} trim, pushed to high saturation.\n"
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
    ap.add_argument("--anchor", default="liu_zhengfeng", help="當風格錨的角色 id")
    ap.add_argument("--force", action="store_true", help="已存在也重畫")
    args = ap.parse_args()

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
        prompt = build_prompt(a, POSE[aid])
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
        print("接著跑：python art/embed_portraits.py，把圖壓進兩個網頁")


if __name__ == "__main__":
    main()
