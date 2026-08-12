"""人物設定檔（cast JSON）：把「這場 run 的人是誰」從劇本 .py 裡拉出來。

劇本模組仍然是預設值。給了 `--cast <file>` 就用檔案裡的人取代——改人設、換武功、
搬起始位置、加人、減人，都不必動程式碼。人物工作室（`cast/build_editor.py` 產出的
`cast_editor.html`）產生的就是這個格式。

格式（欄位缺了就沿用劇本預設）：

    {
      "scenario": "jianghu",
      "agents": [
        {"id": "liu_zhengfeng", "name": "劉正風", "role": "villager",
         "home_area": "劉府", "start": [2, 2], "skill": 8, "kin": ["qu_yang"],
         "public": "劉正風，衡山派……",      ← 進世界區塊的公開人物表，人人看得見
         "persona": "你是劉正風……",         ← 只有他自己看得見
         "goals": [{"kind": "ritual", "text": "把金盆洗手辦完",
                    "params": {"rite": "金盆洗手", "by_tick": 60}}],
         "arts": ["jin_pen_xi_shou", "heng_shan_jian"],
         "llm": {"model": "gemini-3.1-flash", "temperature": 0.9, "thinking": "low"},
         "look": {...}}                     ← 長相，引擎不看，工作室與回放頁才用
      ]
    }

`look` 這個欄位以前叫 `art`，和絕技的 `arts` 只差一個 s、又躺在同一份 JSON 裡，
看走眼是遲早的事，所以改名了。**舊檔案照樣讀得進來**：工作室匯入時會把 `art`
當成 `look` 收下（見 `cast/editor.template.html` 的 `adopt()`），存檔時寫成新名。
引擎兩個都不看。

`goals` 的 kind 必須是 `world/goals.py` 認得的判定器；`arts` 裡的 id 必須在
`world/arts.py` 的目錄裡。兩者都在開跑前驗，錯字不會拖到第 40 tick 才發作。
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import PROVIDERS
from .world import arts as arts_mod
from .world import goals as goals_mod
from .world.grid import Grid, Pos
from .world.state import AgentState, Goal, WorldState

THINKING = ("minimal", "low", "medium", "high", "off")


class CastError(ValueError):
    """設定檔本身有問題——在燒掉任何 API 額度之前就該擋下來。"""


def load(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise CastError(f"找不到人物設定檔：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CastError(f"{p} 不是合法的 JSON：{e}") from e
    if not isinstance(data.get("agents"), list) or not data["agents"]:
        raise CastError(f"{p} 裡沒有 agents 陣列，或裡面一個人也沒有")
    return data


def _check_llm(who: str, spec: dict, provider: str | None) -> list[str]:
    """每個人可以自己掛模型與溫度。錯字要在開跑前抓到，不是跑到第 40 tick 才 404。"""
    llm = spec.get("llm")
    if not llm:
        return []
    out: list[str] = []
    if not isinstance(llm, dict):
        return [f"{who}：llm 必須是物件，例如 {{\"model\": ..., \"temperature\": 0.8}}"]
    model = llm.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        out.append(f"{who}：llm.model 要是模型 ID 字串")
    elif model and provider:
        known = set(PROVIDERS[provider]["prices"])
        if model not in known:
            out.append(f"{who}：{provider} 沒有列到模型 {model!r}，"
                       f"價格會算成 0（跑得動但帳不準）。用 `truman.cli models` 對一次")
    t = llm.get("temperature")
    if t is not None and (not isinstance(t, (int, float)) or not 0 <= t <= 2):
        out.append(f"{who}：溫度 {t!r} 要是 0–2 之間的數字")
    th = llm.get("thinking")
    if th is not None and th not in THINKING:
        out.append(f"{who}：thinking {th!r} 不在 {list(THINKING)} 裡")
    return out


def _check_arts(who: str, spec: dict) -> list[str]:
    """絕技 id 打錯字的話，那個人會安安靜靜地少一門功夫——要在開跑前抓到。"""
    ids = spec.get("arts")
    if ids is None:
        return []
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
        return [f"{who}：arts 要是絕技 id 的字串陣列，例如 [\"kuai_dao\"]"]
    out = []
    for bad in arts_mod.unknown(ids):
        out.append(f"{who}：絕技目錄裡沒有 {bad!r}。可用的有："
                   f"{'、'.join(sorted(arts_mod.CATALOG))}")
    if len(set(ids)) != len(ids):
        out.append(f"{who}：同一門絕技配了兩次")
    return out


def _check_goals(who: str, spec: dict, ids: set[str], grid: Grid) -> list[str]:
    """目的的參數指到不存在的人或地方，判定會永遠不成立——那比報錯更難查。"""
    goals = spec.get("goals")
    if goals is None:
        return []
    if not isinstance(goals, list):
        return [f"{who}：goals 要是陣列"]
    out = []
    for i, g in enumerate(goals, 1):
        tag = f"{who} 的第 {i} 個目的"
        if not isinstance(g, dict):
            out.append(f"{tag}：要是物件，至少有 kind 和 text")
            continue
        kind = g.get("kind")
        if kind not in goals_mod.CHECKERS:
            out.append(f"{tag}：不認得的判定器 {kind!r}。可用的有："
                       f"{'、'.join(sorted(goals_mod.CHECKERS))}")
            continue
        if not (g.get("text") or "").strip():
            out.append(f"{tag}：text 是空的（那是角色自己讀的那句話，不能省）")
        p = g.get("params") or {}
        if not isinstance(p, dict):
            out.append(f"{tag}：params 要是物件")
            continue
        # 指到人的參數
        refs = []
        if kind in ("isolate",):
            refs.append(p.get("target"))
        if kind in ("meet",):
            refs.append(p.get("who"))
        if kind in ("prevent",):
            refs.append(p.get("agent"))
        if kind == "protect":
            refs.extend(p.get("who") or [])
        for r in refs:
            if not r:
                out.append(f"{tag}：{kind} 少了指定對象的參數")
            elif r not in ids:
                out.append(f"{tag}：指到的 {r!r} 不在這份名單裡")
        # 指到地方的參數
        areas = []
        if kind == "reach":
            if not p.get("area"):
                out.append(f"{tag}：reach 要指定 area")
            else:
                areas.append(p["area"])
        areas.extend(p.get("areas") or [])
        for area in areas:
            if area != grid.street and grid.area(area) is None:
                out.append(f"{tag}：{area!r} 不是這張地圖上的區域")
    return out


def validate(cast: dict, grid: Grid, scenario_name: str | None = None,
             provider: str | None = None) -> list[str]:
    """回傳所有問題（不是丟第一個）——一次看完比修一個跑一次快。"""
    problems: list[str] = []
    agents = cast["agents"]
    ids: set[str] = set()

    scen = cast.get("scenario")
    if scenario_name and scen and scen != scenario_name:
        problems.append(f"設定檔寫的是劇本 {scen!r}，但這次跑的是 {scenario_name!r}")

    for i, a in enumerate(agents):
        who = a.get("name") or a.get("id") or f"第 {i + 1} 個"
        aid = a.get("id", "")
        if not aid:
            problems.append(f"{who}：沒有 id")
        elif aid in ids:
            problems.append(f"{who}：id {aid!r} 重複了")
        else:
            ids.add(aid)
        if not (a.get("name") or "").strip():
            problems.append(f"{aid or who}：沒有名字")
        if not (a.get("persona") or "").strip():
            problems.append(f"{who}：人設是空的")

        start = a.get("start")
        if start is not None:
            try:
                p = Pos.of(start)
            except (TypeError, ValueError, IndexError):
                problems.append(f"{who}：start {start!r} 不是 [x, y]")
            else:
                if not grid.in_bounds(p):
                    problems.append(f"{who}：起始位置 {p} 在地圖外")
                elif not grid.walkable(p):
                    problems.append(f"{who}：起始位置 {p} 是 {grid.terrain(p)}，站不上去")

        home = a.get("home_area")
        if home and home != grid.street and grid.area(home) is None:
            problems.append(f"{who}：home_area {home!r} 不是這張地圖上的區域")

        skill = a.get("skill")
        if skill is not None and not (isinstance(skill, int) and 1 <= skill <= 10):
            problems.append(f"{who}：武功 {skill!r} 必須是 1–10 的整數")
        problems.extend(_check_llm(who, a, provider))
        problems.extend(_check_arts(who, a))

    # 目的會指到別人，所以要等 ids 收完整才驗。
    for a in agents:
        who = a.get("name") or a.get("id") or "?"
        problems.extend(_check_goals(who, a, ids, grid))
        for k in a.get("kin") or []:
            if k not in ids:
                problems.append(f"{a.get('name', a.get('id'))}：在意的人 {k!r} 不在這份名單裡")

    seen: dict[tuple[int, int], str] = {}
    for a in agents:
        if a.get("start") is None:
            continue
        key = tuple(a["start"])
        if key in seen:
            problems.append(f"{a.get('name')} 和 {seen[key]} 的起始位置都在 {key}（同一格）")
        else:
            seen[key] = a.get("name", a.get("id", "?"))
    return problems


def apply(world: WorldState, cast: dict, grid: Grid) -> None:
    """就地改寫世界的人物名單：覆蓋、新增、刪掉沒列到的人。

    劇本的預設值當底：只寫了 persona 的人，位置武功都照劇本。
    """
    defaults = dict(world.agents)
    world.agents.clear()
    for spec in cast["agents"]:
        aid = spec["id"]
        base = defaults.get(aid)
        if base is None:
            home = spec.get("home_area") or grid.street
            base = AgentState(
                id=aid,
                name=spec.get("name", aid),
                role=spec.get("role", "villager"),
                persona="",
                home_area=home,
                pos=Pos.of(spec.get("start") or [0, 0]),
            )
        if "name" in spec:
            base.name = spec["name"]
        if spec.get("role"):
            base.role = spec["role"]
        if "persona" in spec:
            base.persona = spec["persona"].strip()
        if spec.get("home_area"):
            base.home_area = spec["home_area"]
        if spec.get("start") is not None:
            base.pos = Pos.of(spec["start"])
        if spec.get("skill") is not None:
            base.skill = int(spec["skill"])
        if "kin" in spec:
            base.kin = list(spec["kin"])
        if "goals" in spec:
            base.goals = [Goal.from_dict(g) for g in spec["goals"] or []]
        if "arts" in spec:
            base.arts = arts_mod.equip(spec["arts"] or [])
        if "llm" in spec:
            base.llm = {k: v for k, v in (spec["llm"] or {}).items() if v not in (None, "")}
        world.agents[aid] = base


def public_cast_text(cast: dict, fallback: str = "") -> str:
    """公開人物表：人人都看得見的那一段，進世界區塊（也進快取前綴）。"""
    lines = []
    for a in cast["agents"]:
        pub = (a.get("public") or "").strip()
        if pub:
            lines.append(pub if pub.startswith("- ") else f"- {pub}")
    return "\n".join(lines) + "\n" if lines else fallback
