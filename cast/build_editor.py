"""人物工作室：把劇本現有的人抓出來，產生一個可離線編輯的頁面。

    python cast/build_editor.py            # 預設 jianghu
    python cast/build_editor.py hakoniwa

輸出 `cast_editor.html`（repo 根目錄，自帶資料、離線可開）。在頁面上改完人設按「下載
cast.json」，然後：

    python -m truman.cli --scenario jianghu run --run-id j2 --cast cast/jianghu.json

公開人物表（PUBLIC_CAST）是一整段文字，這裡用「- 名字，……」的行首把它拆回每個人身上；
拆不出來的行原樣留在 `leftover`，頁面上會顯示提醒。
"""
from __future__ import annotations

import json
import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from art.embed_portraits import portrait_map  # noqa: E402
from art.embed_icons import icon_map  # noqa: E402
from truman.config import PROVIDERS  # noqa: E402
from truman.world import arts as arts_mod  # noqa: E402
from truman.world import goals as goals_mod  # noqa: E402

# 長相：劇本 .py 裡沒有這一層（引擎不需要），預設值放這裡，之後以 cast.json 為準。
ART_DEFAULTS = {
    "liu_zhengfeng": {"sect":"衡山派","title":"劉三爺","color":"#4f9b86","robe":"#3f7d6e","trim":"#25584c",
                      "hair":"#241d16","hat":"bun","beard":"short","weapon":"sword","acc":"jade",
                      "brow":"flat","mouth":"small","goal":"把這個手洗完。誰來說什麼，都忍。"},
    "fei_bin": {"sect":"嵩山派","title":"大嵩陽手","color":"#6f7a94","robe":"#3d4457","trim":"#20242f",
                "hair":"#191510","hat":"official","beard":"none","weapon":"sword",
                "brow":"stern","mouth":"frown","goal":"攔下這場洗手。用什麼手段都可以。"},
    "linghu_chong": {"sect":"華山派","title":"岳不群大弟子","color":"#c9903a","robe":"#b07a1e","trim":"#7d5310",
                     "hair":"#241d16","hat":"bun","beard":"none","weapon":"sword","acc":"gourd",
                     "brow":"raise","mouth":"smirk","goal":"少說話、別惹事——他自己也知道當不得真。"},
    "yi_lin": {"sect":"恆山派","title":"定逸師太弟子","color":"#cf8fa4","robe":"#d9d0bd","trim":"#a58f8f",
               "hair":"none","hat":"nun","beard":"none","weapon":None,"acc":"kasaya",
               "brow":"worry","mouth":"small","goal":"找到師父，或找個安全的地方待著。"},
    "tian_boguang": {"sect":"萬里獨行","title":"快刀無雙","color":"#b84a3c","robe":"#8e3b31","trim":"#5c231d",
                     "hair":"#1b1610","hat":"band","beard":"stub","weapon":"blade",
                     "brow":"raise","mouth":"smirk","extra":"scar","goal":"把那個落單的小尼姑哄到沒人的地方去。"},
    "qu_yang": {"sect":"日月神教","title":"長老","color":"#8b6bab","robe":"#6b4a86","trim":"#42295a",
                "hair":"#ddd6c8","hat":"bun","beard":"long","weapon":"qin",
                "brow":"flat","mouth":"line","goal":"遠遠看他一眼就走。別連累他。"},
    # 海晏鎮（hakoniwa / seahaven 共用）
    "mei_yi": {"sect":"咖啡館","title":"老闆","color":"#c47a8a","robe":"#a85d6e","trim":"#6e3a46",
               "hair":"#2a221c","hat":"none","beard":"none","weapon":None,
               "brow":"raise","mouth":"smirk","goal":"傍晚在店裡辦一場小聚。"},
    "chen_yuan": {"sect":"保險行","title":"業務","color":"#6a9aaa","robe":"#4f7e8c","trim":"#2f4e58",
                  "hair":"#241d16","hat":"none","beard":"none","weapon":None,
                  "brow":"flat","mouth":"smirk","goal":"把壞了三年的傳真機修一修。"},
    "lin_shu": {"sect":"診所","title":"護理師","color":"#7eb6a4","robe":"#d7ebe3","trim":"#5f8f80",
                "hair":"#241d16","hat":"none","beard":"none","weapon":None,
                "brow":"worry","mouth":"small","goal":"把夜班排班的事跟診所說清楚。"},
    "wang_hao": {"sect":"保險行","title":"業務","color":"#d4924a","robe":"#c47a2e","trim":"#8a5218",
                 "hair":"#241d16","hat":"none","beard":"none","weapon":None,
                 "brow":"raise","mouth":"smirk","goal":"找人陪去海堤試新釣竿。"},
    "guo_bo": {"sect":"報攤","title":"老闆","color":"#8a8f98","robe":"#6b7078","trim":"#3f434a",
               "hair":"#ddd6c8","hat":"band","beard":"stub","weapon":None,
               "brow":"flat","mouth":"line","goal":"盤算要不要把攤子收掉。"},
    "su_qing": {"sect":"圖書館","title":"館員","color":"#9a8bb8","robe":"#e8dfd2","trim":"#7a6a92",
                "hair":"#241d16","hat":"none","beard":"none","weapon":None,
                "brow":"flat","mouth":"small","goal":"整理舊報紙合訂本，貼幾則舊聞。"},
    # 嵐潮鎮（tempest）
    "shen_xi": {"sect":"鎮廟","title":"金水伯","color":"#3d6b7a","robe":"#2a4a56","trim":"#1a3038",
                "hair":"#3a342c","hat":"none","beard":"short","weapon":None,"acc":"beads",
                "brow":"flat","mouth":"line","goal":"在鎮廟當眾做完海醮。空廟不算。"},
    "shi_lei": {"sect":"鐵鋪","title":"鐵雄","color":"#b85a2a","robe":"#5a4035","trim":"#2a1f18",
                "hair":"#1b1610","hat":"none","beard":"stub","weapon":"hammer",
                "brow":"stern","mouth":"line","goal":"在海堤當眾焊死水閘。要有人作證。"},
    "fang_lan": {"sect":"村長宅","title":"美華姐","color":"#4a7a6a","robe":"#3d5c52","trim":"#243832",
                 "hair":"#241d16","hat":"none","beard":"none","weapon":None,
                 "brow":"stern","mouth":"small","goal":"把人趕到該去的地方：廟、堤、高地。"},
    "a_qian": {"sect":"漁港","title":"阿海","color":"#2f6f8f","robe":"#1e4a5c","trim":"#0f2a36",
               "hair":"#1b1610","hat":"band","beard":"none","weapon":None,"acc":"rope",
               "brow":"raise","mouth":"smirk","goal":"先回漁港收船，再跟阿德對潮訊。"},
    "qing_he": {"sect":"漁市","title":"草藥醫","color":"#6a8f5a","robe":"#d4c9a8","trim":"#5f7a4a",
                "hair":"#241d16","hat":"none","beard":"none","weapon":None,"acc":"pouch",
                "brow":"worry","mouth":"small","goal":"自己先上高地，並跟村長說清楚疏散。"},
    "gu_chao": {"sect":"海堤","title":"阿德","color":"#6a7078","robe":"#4a5058","trim":"#2c3036",
                "hair":"#3a342c","hat":"band","beard":"stub","weapon":None,
                "brow":"flat","mouth":"line","goal":"守住海堤視線，把鐵雄找回來焊水門。"},
}
FALLBACK_ART = {"sect":"","title":"","color":"#8a8272","robe":"#6f6455","trim":"#463f34",
                "hair":"#241d16","hat":"bun","beard":"none","weapon":None,"acc":None,"brow":"flat","mouth":"line","extra":None,"goal":""}


def split_public_cast(text: str, names: list[str]) -> tuple[dict[str, str], list[str]]:
    """把 PUBLIC_CAST 一段文字拆成每個人一段。回傳 (依名字, 拆不出來的行)。"""
    chunks: dict[str, str] = {}
    leftover: list[str] = []
    cur: str | None = None
    for line in (text or "").splitlines():
        if line.startswith("- "):
            head = line[2:]
            cur = next((n for n in names if head.startswith(n)), None)
            if cur:
                chunks[cur] = line
            else:
                leftover.append(line)
        elif cur and line.strip():
            chunks[cur] += "\n" + line.rstrip()
        elif line.strip():
            leftover.append(line)
    return chunks, leftover


def main(scenario: str = "jianghu") -> None:
    scen = import_module(f"scenarios.{scenario}")
    grid = scen.build_grid()
    names = [a["name"] for a in scen.AGENTS]
    public, leftover = split_public_cast(getattr(scen, "PUBLIC_CAST", ""), names)

    agents = []
    for a in scen.AGENTS:
        look = dict(FALLBACK_ART, **ART_DEFAULTS.get(a["id"], {}))
        agents.append({
            "id": a["id"], "name": a["name"], "role": a.get("role", "villager"),
            "home_area": a["home_area"], "start": list(a["start"]),
            "skill": a.get("skill", 5), "kin": list(a.get("kin", [])),
            "public": public.get(a["name"], ""),
            "persona": a["persona"].rstrip(),
            # 目的與絕技：劇本給的預設。工作室改完存回來，--cast 就能直接跑。
            "goals": [dict(g) for g in a.get("goals", [])],
            "arts": list(a.get("arts", [])),
            "llm": {},
            # 長相。舊檔案裡這個欄位叫 `art`，和絕技的 `arts` 只差一個 s，
            # 兩個都在同一份 JSON 裡，看走眼是遲早的事——所以改叫 `look`。
            # 讀的那一邊仍然收得下舊名（見工作室的 adopt()）。
            "look": look,
        })

    data = {
        "scenario": scen.NAME,
        "title": getattr(scen, "TITLE", scen.NAME),
        "combat": bool(getattr(scen, "COMBAT", False)),
        "rows": scen.GRID_ROWS,
        "legend": {sym: {"name": n, "walk": w} for sym, (n, w) in scen.LEGEND.items()},
        "areas": [{"name": a.name, "x0": a.x0, "y0": a.y0, "x1": a.x1, "y1": a.y1,
                   "desc": a.description} for a in grid.areas.values()],
        "street": grid.street,
        "agents": agents,
        "leftover_public": leftover,
        "art_defaults": {"fallback": FALLBACK_ART},
        # 絕技目錄與目的判定器：工作室要靠這兩份才做得出「挑工具」的介面。
        # 說明文字直接取自 ArtDef，和角色實際讀到的 prompt 是同一份字，不會走鐘。
        "arts_catalog": [
            {"id": d.id, "name": d.name, "kind": d.kind, "tagline": d.tagline,
             "when": d.when, "uses": d.uses, "cooldown": d.cooldown,
             "target": d.target, "reach": d.reach, "combat_only": d.combat_only,
             "cost": d.cost_line()}
            for d in sorted(arts_mod.CATALOG.values(), key=lambda x: (x.kind, x.id))
        ],
        "art_kinds": {"combat": "戰鬥", "social": "社交", "info": "情報", "move": "身法"},
        "goal_kinds": sorted(goals_mod.CHECKERS),
        "portraits": portrait_map(scen.NAME),
        "art_icons": icon_map(scen.NAME),
        # 模型目錄：讓工作室的下拉選單有東西可選，並且能就地估價
        "providers": {
            name: {
                "tiers": p["models"],
                "models": sorted(p["prices"]),
                "prices": {m: list(v) for m, v in p["prices"].items()},
            } for name, p in PROVIDERS.items()
        },
    }

    # 劇本原設定也存成一份 cast.json：可以直接拿去 --cast 跑，也方便 diff 自己改了什麼
    ref = Path(__file__).with_name(f"{scen.NAME}.default.json")
    ref.write_text(json.dumps({"scenario": scen.NAME, "agents": agents},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {ref} （劇本原設定，可直接 --cast）")

    tpl = Path(__file__).with_name("editor.template.html").read_text(encoding="utf-8")
    for marker in ("/*__DATA__*/ null", "/*__PIXELART__*/"):
        if marker not in tpl:
            raise SystemExit(f"editor.template.html 裡找不到注入點 {marker!r}")
    html = tpl.replace("/*__PIXELART__*/", (ROOT / "web" / "pixelart.js").read_text(encoding="utf-8"))
    html = html.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    out = ROOT / "cast_editor.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size/1024:.1f} KB) — {len(agents)} 人，劇本 {scen.NAME}")
    if leftover:
        print(f"⚠ PUBLIC_CAST 有 {len(leftover)} 行對不到人，頁面上會標出來")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "jianghu")
