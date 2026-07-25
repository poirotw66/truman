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
         "art": {...}}                      ← 純美術，引擎不看，回放頁才用
      ]
    }

`art` 引擎完全不碰，原樣留著給回放頁讀。
"""

from __future__ import annotations

import json
from pathlib import Path

from .world.grid import Grid, Pos
from .world.state import AgentState, WorldState


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


def validate(cast: dict, grid: Grid, scenario_name: str | None = None) -> list[str]:
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

    for a in agents:
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
        world.agents[aid] = base


def public_cast_text(cast: dict, fallback: str = "") -> str:
    """公開人物表：人人都看得見的那一段，進世界區塊（也進快取前綴）。"""
    lines = []
    for a in cast["agents"]:
        pub = (a.get("public") or "").strip()
        if pub:
            lines.append(pub if pub.startswith("- ") else f"- {pub}")
    return "\n".join(lines) + "\n" if lines else fallback
