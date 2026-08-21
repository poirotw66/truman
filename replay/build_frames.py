"""把一場 run 的事件日誌重建成回放用的逐 tick frames，並產出自帶資料的 HTML。

沒有 API 呼叫。座標優先讀日誌裡的 `snapshot` 事件——引擎每個 tick 記一次全員位置／
傷勢／生死，那是**真值**。舊的 run（snapshot 是後來才加的）才退回「把 move_to 意圖
丟回同一套 BFS 重放、再拿 checkpoint 對答案」的近似法。
其餘（心裡話／對話／動手／死亡／導演旁白）直接從事件日誌撈。

用法：
    python replay/build_frames.py                 # 預設 j1(0-47) + j1b(48-)
    python replay/build_frames.py --run j2        # 單一 run
    python replay/build_frames.py --run t1 --out t1_replay.html
輸出：
    replay/frames.json      中繼資料（可關）
    <out>                   自帶資料、離線可開的回放頁
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from art.embed_portraits import portrait_map  # noqa: E402
from art.embed_scenes import scene_map  # noqa: E402
from art.embed_icons import icon_map  # noqa: E402
from art.embed_event_icons import event_icon_map  # noqa: E402
from truman.world import arts as arts_mod  # noqa: E402
from truman.world.grid import Pos  # noqa: E402

DEFAULT_SOURCES = [("j1", 0, 48), ("j1b", 48, 10**9)]
DEFAULT_CHECKPOINTS = [("j1", (12, 24, 36, 48)), ("j1b", (60, 72, 84, 96))]
MOVE_SPEED = 3

# 回放標題／開場文案。沒列到的劇本用 NAME。
SCENARIO_META = {
    "jianghu": {
        "title": "衡山城 · 劉正風金盆洗手",
        "lede": (
            "六個 AI 被放進同一天的同一座城。沒有人寫結局——目標互相排斥，張力自己會長出來。"
            "<br>知識對稱，每個人只看得見自己看得見的、只記得自己記得的；而這個世界允許他們拔刀。"
        ),
        "foot": (
            "箱庭實錄：目標互相排斥的多智能體，知識對稱、世界允許拔刀；沒有預寫結局。<br>"
            "離線確定性重放，零 API 呼叫。人物取自金庸《笑傲江湖》。"
        ),
        "cta": "▶ 進　城",
        "decor": "jianghu",
        "basin": [2.5, 2.5],
    },
    "tempest": {
        "title": "嵐潮鎮 · 暴潮來襲前的半日",
        "lede": (
            "七個人被丟進同一場暴潮。對手不是刀，是海——"
            "做海醮要人看醮，焊水門要人作證，外海還有阿旺那條船趕不趕得回來。"
            "<br>亥時一到，禮與閘成不成會真的改地圖：低處淹水、斷路、捲走還站在水裡的人。"
        ),
        "foot": (
            "箱庭實錄：壯闊的和平劇本，對手是海；禮／閘成敗會淹鎮。<br>"
            "離線確定性重放，零 API 呼叫。"
        ),
        "cta": "▶ 上　堤",
        "decor": "",
        "basin": None,
    },
}

# 嵐潮地圖符號 → 像素美術認得的符號（衡山那套圖磚）。
# 海 `~` 另外在 pixelart 裡畫水；其餘就近映射，避免碼頭 `p` 被畫成院子屏風。
TEMPEST_REPLAY_SYM = {
    "a": "y",  # 高地 → 泥地／坡
    "b": "l",  # 村長宅 → 廳堂
    "c": "c",  # 鎮廟 → 廟
    "d": "s",  # 廣場 → 青石
    "e": "y",  # 鐵鋪
    "f": "m",  # 漁市 → 市集
    "g": "y",  # 糧倉（避開城門 g）
    "h": "s",  # 海堤 → 石面
    "p": "y",  # 碼頭（避開院子 p）
    # 外海漁船甲板：保留 o，pixelart 畫成「海上的木甲板」，勿映射成泥地 y。
}


def _scenes_for(scenario_name: str) -> dict:
    scenes = scene_map(scenario_name)
    # 外海甲板沒有專圖，借用漁港碼頭視角。
    if scenario_name == "tempest" and "漁港" in scenes:
        scenes.setdefault("外海漁船", scenes["漁港"])
    return scenes


PROFILE_COLORS = [
    "#4f9b86", "#6f7a94", "#c9903a", "#cf8fa4", "#b84a3c", "#8b6bab",
    "#5a8fbf", "#9a7b4f",
]


def fork_tick(run: str) -> int | None:
    """這個 run 是從第幾拍 fork 出來的（沒有 fork 事件就是從頭跑的原始 run）。"""
    path = ROOT / "runs" / run / "events.jsonl"
    if not path.exists():
        return None
    for line in path.open(encoding="utf-8"):
        if '"fork"' not in line:
            continue
        r = json.loads(line)
        if r["type"] == "fork":
            return r["data"]["at_tick"]
    return None


def resolve_sources(runs: list[str] | None) -> tuple[list[tuple[str, int, int]], list[tuple[str, tuple[int, ...]]]]:
    """回傳 (SOURCES, CHECKPOINTS)。runs=None 用預設 j1+j1b 接力。"""
    if not runs:
        return list(DEFAULT_SOURCES), list(DEFAULT_CHECKPOINTS)

    starts = [fork_tick(r) or 0 for r in runs]
    bounds = starts[1:] + [10**9]
    sources = [(r, lo, hi) for r, lo, hi in zip(runs, starts, bounds)]
    checkpoints = [
        (
            r,
            tuple(
                t
                for t in (
                    int(p.stem[1:])
                    for p in sorted((ROOT / "runs" / r / "checkpoints").glob("t*.json"))
                )
                if lo <= t - 1 < hi
            ),
        )
        for r, lo, hi in sources
        if (ROOT / "runs" / r / "checkpoints").exists()
    ]
    return sources, checkpoints


def detect_scenario(run: str) -> str:
    """從 run_start 或 checkpoint 讀劇本名；讀不到就當江湖。"""
    path = ROOT / "runs" / run / "events.jsonl"
    if path.exists():
        for line in path.open(encoding="utf-8"):
            if '"run_start"' not in line:
                continue
            r = json.loads(line)
            if r.get("type") == "run_start":
                return r.get("data", {}).get("scenario") or "jianghu"
    cps = sorted((ROOT / "runs" / run / "checkpoints").glob("t*.json"))
    if cps:
        try:
            return json.loads(cps[0].read_text(encoding="utf-8")).get("scenario") or "jianghu"
        except (OSError, json.JSONDecodeError):
            pass
    return "jianghu"


def load_scenario(name: str):
    return importlib.import_module(f"scenarios.{name}")


def make_renamer(scen):
    """回傳文字替換函式：把日誌裡的舊名换成劇本現在的名字。

    有些劇本的角色改過名（例如嵐潮的 t1/t2 兩場是改名前跑的），但事件日誌
    是實跑紀錄，不能回頭改。劇本模組可以宣告一張 `RENAMED = {舊名: 現名}`
    的表，這裡讀出來、包成一個字串替換函式；沒有 RENAMED 的劇本（例如
    江湖）拿到的是空表，函式原樣傳回文字，完全不影響顯示。
    """
    renamed: dict[str, str] = getattr(scen, "RENAMED", {}) or {}
    if not renamed:
        return lambda s: s

    def _rn(s):
        if not s:
            return s
        for old, new in renamed.items():
            if old in s:
                s = s.replace(old, new)
        return s

    return _rn


def replay_rows(scen) -> list[str]:
    """給像素地圖用的 rows：嵐潮符號映射到現有圖磚。"""
    rows = list(scen.GRID_ROWS)
    if getattr(scen, "NAME", "") != "tempest":
        return rows
    return ["".join(TEMPEST_REPLAY_SYM.get(ch, ch) for ch in row) for row in rows]


def walk_between(grid, a: Pos, b: Pos, limit: int = 8) -> list[list[int]]:
    """兩個已知位置之間的最短路徑（不含起點）。"""
    if a == b:
        return []
    prev = {a: None}
    q = deque([a])
    while q:
        cur = q.popleft()
        if cur == b:
            out = []
            node = cur
            while node is not None and node != a:
                out.append([node.x, node.y])
                node = prev[node]
            out.reverse()
            return out if len(out) <= limit else [[b.x, b.y]]
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = Pos(cur.x + d[0], cur.y + d[1])
            if nxt not in prev and grid.walkable(nxt):
                prev[nxt] = cur
                q.append(nxt)
    return [[b.x, b.y]]


def _read_events(run: str):
    for line in (ROOT / "runs" / run / "events.jsonl").open(encoding="utf-8"):
        line = line.strip()
        if line:
            yield json.loads(line)


def _load_cp(run: str, t: int):
    p = ROOT / "runs" / run / "checkpoints" / f"t{t:05d}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return {aid: Pos.of(a["pos"]) for aid, a in d["agents"].items()}


def _load_cast_looks(scenario_name: str) -> dict[str, dict]:
    """讀 cast/<scenario>.default.json 的 look（像素小人外貌）。沒有就空。"""
    path = ROOT / "cast" / f"{scenario_name}.default.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for agent in data.get("agents") or []:
        aid = agent.get("id")
        look = agent.get("look") or agent.get("art")
        if aid and isinstance(look, dict):
            out[str(aid)] = look
    return out


def _cast_profiles(scen) -> list[dict]:
    """回放用的人物卡：缺 skill／kin 的劇本給預設，並帶上開場文案欄位。"""
    looks = _load_cast_looks(getattr(scen, "NAME", "") or "")
    cast = []
    for i, spec in enumerate(scen.AGENTS):
        goals = spec.get("goals") or []
        goal_text = goals[0]["text"] if goals else ""
        persona = (spec.get("persona") or "").strip()
        blurb = ""
        for line in persona.splitlines():
            line = line.strip()
            if line and not line.startswith("你叫"):
                blurb = line
                break
        if not blurb and persona:
            blurb = persona.splitlines()[0].strip()
        role = spec.get("role", "")
        look = looks.get(spec["id"], {})
        title = (
            look.get("title")
            or (spec.get("home_area", "") if role in ("villager", "actor", "") else role)
        )
        cast.append({
            "id": spec["id"],
            "name": spec["name"],
            "skill": int(spec.get("skill", 5)),
            "home": spec["home_area"],
            "kin": list(spec.get("kin", [])),
            "arts": [
                {"id": d.id, "name": d.name, "kind": d.kind, "tagline": d.tagline,
                 "cost": d.cost_line()}
                for d in (arts_mod.get(x) for x in spec.get("arts", []))
                if d is not None
            ],
            "ch": spec["name"][0],
            "sect": look.get("sect") or spec.get("home_area", ""),
            "title": title,
            "color": look.get("color") or PROFILE_COLORS[i % len(PROFILE_COLORS)],
            "goal": look.get("goal") or goal_text,
            "blurb": blurb,
            # 地圖 HD-2D 小人外貌（沒有就讓前端 PIXEL_FALLBACK 接手）
            "look": look,
        })
    return cast


def build_replay(
    runs: list[str] | None = None,
    out: Path | str | None = None,
    *,
    write_frames_json: bool = False,
    quiet: bool = False,
) -> Path:
    """從 runs 的 events.jsonl 建回放 HTML，回傳輸出路徑。"""
    sources, checkpoint_specs = resolve_sources(runs)
    scenario_name = detect_scenario(sources[0][0])
    scen = load_scenario(scenario_name)
    rn = make_renamer(scen)
    meta = SCENARIO_META.get(scenario_name, {
        "title": getattr(scen, "NAME", scenario_name),
        "lede": "一場多智能體箱庭實錄。",
        "foot": "離線確定性重放，零 API 呼叫。",
        "cta": "▶ 開　始",
        "decor": "",
        "basin": None,
    })

    out_path = Path(out) if out else ROOT / f"{scenario_name}_replay.html"
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    for run, lo, hi in sources:
        ev = ROOT / "runs" / run / "events.jsonl"
        if not ev.exists():
            raise FileNotFoundError(f"找不到 {ev}")
        if not quiet:
            print(f"  {run}: tick [{lo}, {'∞' if hi > 10**8 else hi})  scenario={scenario_name}")

    grid = scen.build_grid()
    agents = {a["id"]: a for a in scen.AGENTS}
    start = {a["id"]: Pos(*a["start"]) for a in scen.AGENTS}

    events = []
    for run, lo, hi in sources:
        for r in _read_events(run):
            if r["tick"] is not None and lo <= r["tick"] < hi:
                events.append(r)
    if not events:
        raise ValueError("沒有可合併的事件")
    events.sort(key=lambda r: (r["tick"], r["seq"]))
    max_tick = max(r["tick"] for r in events)
    if not quiet:
        print("merged events:", len(events), "max_tick:", max_tick)

    by_tick: dict[int, list] = {}
    for r in events:
        by_tick.setdefault(r["tick"], []).append(r)

    snaps: dict[int, dict] = {}
    for r in events:
        if r["type"] == "snapshot":
            snaps[r["tick"]] = r["data"]["agents"]
    use_snapshot = len(snaps) > max_tick * 0.9
    if not quiet:
        print(
            f"snapshot 事件：{len(snaps)} / {max_tick + 1} tick"
            f" → 位置來源：{'snapshot（真值）' if use_snapshot else 'BFS 重建（近似，舊 run）'}"
        )

    thought_at: dict[tuple[int, str], str] = {}
    for r in events:
        if r["type"] == "llm_call" and isinstance(r["data"].get("output"), dict):
            parts = r["data"].get("key", "").split(":")
            if len(parts) >= 3 and parts[2] in ("act", "reply"):
                th = (r["data"]["output"].get("thought") or "").strip()
                if th:
                    thought_at[(int(parts[0]), parts[1])] = rn(th)

    checkpoints = {}
    for run, ts in checkpoint_specs:
        for t in ts:
            cp = _load_cp(run, t)
            if cp and t > 0:
                checkpoints[t - 1] = cp

    pos = {aid: start[aid] for aid in agents}
    action: dict[str, dict | None] = {aid: None for aid in agents}
    wound = {aid: 0 for aid in agents}
    alive = {aid: True for aid in agents}

    frames = []
    drift = 0
    n_speech = n_attack = n_death = 0
    n_art = n_goal_done = 0
    goal_log: list[dict] = []
    walked = {aid: 0 for aid in agents}
    flood_tick = -1
    flooded: list[list[int]] = []
    outcome = ""
    outcome_text = ""
    epilogue: dict = {}

    # epilogue 可能 tick=null（補寫），不進 by_tick；直接從各 run 原文取最後一筆
    for run, _, _ in sources:
        for r in _read_events(run):
            if r.get("type") == "epilogue" and isinstance(r.get("data"), dict):
                epilogue = r["data"]

    for t in range(0, max_tick + 1):
        evs = by_tick.get(t, [])
        tick_events: list[dict] = []

        for r in evs:
            d, ty = r["data"], r["type"]
            if ty == "intent" and d.get("kind") == "move_to":
                aid = d["agent"]
                if alive.get(aid, False):
                    action[aid] = {"path": grid.path(pos[aid], d["target"]), "to": d["target"]}
            elif ty == "intent" and d.get("kind") == "interact":
                action[d["agent"]] = None
                tick_events.append(
                    {"kind": "interact", "agent": d["agent"], "obj": rn(d.get("object", ""))}
                )
            elif ty == "speech":
                action[d["speaker"]] = None
                n_speech += 1
                tick_events.append(
                    {
                        "kind": "speech",
                        "agent": d["speaker"],
                        "name": rn(d["speaker_name"]),
                        "to": d.get("to"),
                        "text": rn(d["utterance"]),
                    }
                )
            elif ty == "attack":
                action[d["attacker"]] = None
                n_attack += 1
                tick_events.append(
                    {
                        "kind": "attack",
                        "agent": d["attacker"],
                        "target": d["target"],
                        "line": rn(d["line"]),
                        "margin": d.get("margin"),
                    }
                )
                wound[d["target"]] = d.get("target_wound", wound[d["target"]])
                wound[d["attacker"]] = d.get("attacker_wound", wound[d["attacker"]])
            elif ty == "invalid_intent":
                action[d["agent"]] = None
            elif ty == "death":
                alive[d["agent"]] = False
                wound[d["agent"]] = 3
                action[d["agent"]] = None
                n_death += 1
                tick_events.append(
                    {
                        "kind": "death",
                        "agent": d["agent"],
                        "name": rn(d["name"]),
                        "killed_by": rn(d.get("killed_by", "")),
                    }
                )
            elif ty == "art_used":
                action[d["agent"]] = None
                n_art += 1
                tick_events.append(
                    {
                        "kind": "art",
                        "agent": d["agent"],
                        "art": d.get("art_name", d.get("art", "")),
                        "art_id": d.get("art", ""),
                        "art_kind": d.get("kind", ""),
                        "target": d.get("target", ""),
                        "line": rn(d.get("line", "")),
                        "left": d.get("uses_left", -1),
                    }
                )
            elif ty in ("goal_done", "goal_failed"):
                ok = ty == "goal_done"
                n_goal_done += ok
                goal_log.append({
                    "tick": t, "agent": d["agent"], "name": rn(d.get("name", "")),
                    "idx": d.get("goal", 0), "text": rn(d.get("text", "")),
                    "kind": d.get("kind", ""), "done": ok, "note": rn(d.get("note", "")),
                })
                tick_events.append(
                    {
                        "kind": "goal",
                        "agent": d["agent"],
                        "done": ok,
                        "text": rn(d.get("text", "")),
                        "note": rn(d.get("note", "")),
                    }
                )
            elif ty == "reflection":
                ins = d.get("insights") or []
                if ins:
                    tick_events.append(
                        {"kind": "reflection", "agent": d["agent"], "insight": rn(ins[0])}
                    )
            elif ty == "storm":
                flood_tick = t
                outcome = d.get("outcome", "") or outcome
                outcome_text = rn(d.get("text", "")) or outcome_text
                cells = d.get("flooded") or []
                if cells:
                    flooded = [list(xy) for xy in cells]
                    grid.flood_positions(Pos(int(xy[0]), int(xy[1])) for xy in flooded)
                elif d.get("flooded_areas"):
                    names = list(d["flooded_areas"])
                    flooded = [p.as_list() for p in grid.cells_in_areas(names)]
                    grid.flood_positions(Pos(xy[0], xy[1]) for xy in flooded)
                tick_events.append({
                    "kind": "world",
                    "area": "",
                    "text": rn(d.get("text")) or f"暴潮結算：{d.get('outcome', '')}",
                })
            elif ty == "director" and d.get("fired") and d.get("text"):
                if d.get("kind") == "inject":
                    if d.get("tag") == "goal":
                        continue
                    tick_events.append(
                        {"kind": "note", "agent": d.get("agent", ""),
                         "tag": d.get("tag", ""), "text": rn(d["text"])}
                    )
                else:
                    tick_events.append(
                        {"kind": "world", "area": d.get("area", ""), "text": rn(d["text"])}
                    )

        steps: dict[str, list[list[int]]] = {}
        if use_snapshot and t in snaps:
            for aid, snap in snaps[t].items():
                if aid not in agents:
                    continue
                nxt = Pos.of(snap["pos"])
                if nxt != pos[aid]:
                    steps[aid] = walk_between(grid, pos[aid], nxt)
                    walked[aid] += len(steps[aid])
                    pos[aid] = nxt
                wound[aid] = snap.get("wound", wound[aid])
                alive[aid] = snap.get("alive", alive[aid])
        else:
            for aid in agents:
                act = action[aid]
                if alive[aid] and act and act["path"]:
                    taken = act["path"][:MOVE_SPEED]
                    steps[aid] = [[p.x, p.y] for p in taken]
                    walked[aid] += len(taken)
                    pos[aid] = taken[-1]
                    act["path"] = act["path"][MOVE_SPEED:]
                    if not act["path"]:
                        action[aid] = None

            if t in checkpoints:
                for aid, cp_pos in checkpoints[t].items():
                    if aid not in pos:
                        continue
                    if pos[aid] != cp_pos:
                        drift += 1
                        pos[aid] = cp_pos
                        steps[aid] = [[cp_pos.x, cp_pos.y]]

        agents_frame = {}
        for aid in agents:
            cell = {
                "x": pos[aid].x,
                "y": pos[aid].y,
                "area": grid.area_at(pos[aid]),
                "wound": wound[aid],
                "alive": alive[aid],
            }
            if aid in steps:
                cell["steps"] = steps[aid]
            th = thought_at.get((t, aid))
            if th:
                cell["thought"] = th
            agents_frame[aid] = cell
        frames.append({"tick": t, "agents": agents_frame, "events": tick_events})

    if not quiet:
        if use_snapshot:
            bad = sum(
                1
                for t, cp in checkpoints.items()
                for aid, cp_pos in cp.items()
                if aid in agents and t < len(frames)
                and Pos.of(
                    [frames[t]["agents"][aid]["x"], frames[t]["agents"][aid]["y"]]
                )
                != cp_pos
            )
            print(
                f"snapshot vs checkpoint：{bad} 處不一致"
                + ("（完全吻合）" if not bad else " ⚠")
            )
        else:
            print(f"drift vs checkpoints: {drift} cell-mismatches (snapped)")

    if not outcome:
        for run, _, _ in sources:
            cps = sorted((ROOT / "runs" / run / "checkpoints").glob("t*.json"))
            if not cps:
                continue
            try:
                last = json.loads(cps[-1].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            outcome = last.get("outcome") or outcome
            outcome_text = rn(last.get("outcome_text") or "") or outcome_text
            if last.get("flooded") and not flooded:
                flooded = [list(xy) for xy in last["flooded"]]
                if flood_tick < 0:
                    flood_tick = max_tick

    legend = {sym: {"name": n, "walk": w} for sym, (n, w) in scen.LEGEND.items()}
    areas = [
        {
            "name": a.name,
            "x0": a.x0,
            "y0": a.y0,
            "x1": a.x1,
            "y1": a.y1,
            "desc": a.description,
        }
        for a in grid.areas.values()
    ]
    cast = _cast_profiles(scen)

    payload = {
        "scenario": scenario_name,
        "title": meta["title"],
        "lede": meta["lede"],
        "foot": meta["foot"],
        "cta": meta["cta"],
        "decor": meta.get("decor") or "",
        "basin": meta.get("basin"),
        "rows": replay_rows(scen),
        "legend": legend,
        "areas": areas,
        "cast": cast,
        "street": grid.street,
        "frames": frames,
        "max_tick": max_tick,
        "goals": goal_log,
        "flood_tick": flood_tick,
        "flooded": flooded,
        "outcome": outcome,
        "outcome_text": outcome_text,
        "epilogue": {
            "label": (epilogue.get("label") or "").strip(),
            "blurb": (epilogue.get("blurb") or "").strip(),
            "commentary": (epilogue.get("commentary") or "").strip(),
        } if epilogue.get("label") and epilogue.get("blurb") and epilogue.get("commentary") else None,
        "stats": {
            "speeches": n_speech,
            "attacks": n_attack,
            "deaths": n_death,
            "walked": sum(walked.values()),
            "arts": n_art,
            "goals_done": n_goal_done,
            "goals_total": len(goal_log),
        },
        "portraits": portrait_map(scenario_name),
        "scenes": _scenes_for(scenario_name),
        "art_icons": icon_map(scenario_name),
        "event_icons": event_icon_map(),
    }

    if write_frames_json:
        frames_path = Path(__file__).with_name("frames.json")
        frames_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        if not quiet:
            print("wrote", frames_path, f"{frames_path.stat().st_size / 1024:.1f} KB")

    tpl = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    for marker in ("/*__DATA__*/ null", "/*__PIXELART__*/"):
        if marker not in tpl:
            raise RuntimeError(f"template.html 裡找不到注入點 {marker!r}")
    art = (ROOT / "web" / "pixelart.js").read_text(encoding="utf-8")
    html = tpl.replace("/*__PIXELART__*/", art)
    html = html.replace(
        "/*__DATA__*/ null",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    if not quiet:
        print("wrote", out_path, f"{out_path.stat().st_size / 1024:.1f} KB")
        print("\ndeaths:")
        for f in frames:
            for e in f["events"]:
                if e["kind"] == "death":
                    print(f"  t{f['tick']}: {e['name']} ← {e['killed_by']}")
        print(
            f"speeches={n_speech} attacks={n_attack} deaths={n_death} "
            f"steps={sum(walked.values())}"
        )
    return out_path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="事件日誌 → 回放頁")
    ap.add_argument("--run", action="append", help="改用這個 run（可重複，依序接起來）")
    ap.add_argument("--out", default="", help="輸出的 HTML 檔名（預設依劇本名）")
    ap.add_argument(
        "--frames-json", action="store_true",
        help="另外把中繼資料寫成 replay/frames.json（除錯用；產出的 HTML 本來就自帶資料）",
    )
    args = ap.parse_args(argv)
    try:
        build_replay(args.run, args.out or None, write_frames_json=args.frames_json)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
