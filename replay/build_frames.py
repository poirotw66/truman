"""把一場 run 的事件日誌重建成回放用的逐 tick frames，並產出自帶資料的 HTML。

沒有 API 呼叫。座標是把日誌裡的 move_to 意圖丟回引擎同一套 BFS 重放出來的，
再拿每 12 tick 的 checkpoint 對答案（不合就 snap 到真值）。
其餘（心裡話／對話／動手／死亡／導演旁白）直接從事件日誌撈。

用法：
    python replay/build_frames.py            # 預設 j1(0-47) + j1b(48-)
輸出：
    replay/frames.json      中繼資料
    jianghu_replay.html     自帶資料、離線可開的回放頁

要換別場 run，改下面的 SOURCES。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scenarios import jianghu  # noqa: E402
from truman.world.grid import Pos  # noqa: E402

# (run 名稱, 取這個 run 的 tick 範圍 [起, 迄))：j1 跑了前半天，j1b 從 checkpoint fork 續跑
SOURCES = [("j1", 0, 48), ("j1b", 48, 10**9)]
CHECKPOINTS = [("j1", (12, 24, 36, 48)), ("j1b", (60, 72, 84, 96))]

MOVE_SPEED = 3
grid = jianghu.build_grid()

AGENTS = {a["id"]: a for a in jianghu.AGENTS}
START = {a["id"]: Pos(*a["start"]) for a in jianghu.AGENTS}


def read(run: str):
    for line in (ROOT / "runs" / run / "events.jsonl").open(encoding="utf-8"):
        line = line.strip()
        if line:
            yield json.loads(line)


events = []
for run, lo, hi in SOURCES:
    for r in read(run):
        if r["tick"] is not None and lo <= r["tick"] < hi:
            events.append(r)
events.sort(key=lambda r: (r["tick"], r["seq"]))
max_tick = max(r["tick"] for r in events)
print("merged events:", len(events), "max_tick:", max_tick)

by_tick: dict[int, list] = {}
for r in events:
    by_tick.setdefault(r["tick"], []).append(r)

# 心裡話：llm_call 的 output，key 是 "<tick>:<aid>:act|reply"
thought_at: dict[tuple[int, str], str] = {}
for r in events:
    if r["type"] == "llm_call" and isinstance(r["data"].get("output"), dict):
        parts = r["data"].get("key", "").split(":")
        if len(parts) >= 3 and parts[2] in ("act", "reply"):
            th = (r["data"]["output"].get("thought") or "").strip()
            if th:
                thought_at[(int(parts[0]), parts[1])] = th


def load_cp(run: str, t: int):
    p = ROOT / "runs" / run / "checkpoints" / f"t{t:05d}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return {aid: Pos.of(a["pos"]) for aid, a in d["agents"].items()}


checkpoints = {}
for run, ts in CHECKPOINTS:
    for t in ts:
        cp = load_cp(run, t)
        if cp:
            checkpoints[t] = cp

# ---- 重放 ----
pos = {aid: START[aid] for aid in AGENTS}
action: dict[str, dict | None] = {aid: None for aid in AGENTS}
wound = {aid: 0 for aid in AGENTS}
alive = {aid: True for aid in AGENTS}

frames = []
drift = 0
n_speech = n_attack = n_death = 0
walked = {aid: 0 for aid in AGENTS}

for t in range(0, max_tick + 1):
    evs = by_tick.get(t, [])
    tick_events: list[dict] = []

    for r in evs:
        d, ty = r["data"], r["type"]
        if ty == "intent" and d.get("kind") == "move_to":
            aid = d["agent"]
            if alive[aid]:
                action[aid] = {"path": grid.path(pos[aid], d["target"]), "to": d["target"]}
        elif ty == "intent" and d.get("kind") == "interact":
            action[d["agent"]] = None
            tick_events.append({"kind": "interact", "agent": d["agent"], "obj": d.get("object", "")})
        elif ty == "speech":
            action[d["speaker"]] = None
            n_speech += 1
            tick_events.append({"kind": "speech", "agent": d["speaker"], "name": d["speaker_name"],
                                "to": d.get("to"), "text": d["utterance"]})
        elif ty == "attack":
            action[d["attacker"]] = None
            n_attack += 1
            tick_events.append({"kind": "attack", "agent": d["attacker"], "target": d["target"],
                                "line": d["line"], "margin": d.get("margin")})
            wound[d["target"]] = d.get("target_wound", wound[d["target"]])
            wound[d["attacker"]] = d.get("attacker_wound", wound[d["attacker"]])
        elif ty == "invalid_intent":
            action[d["agent"]] = None
        elif ty == "death":
            alive[d["agent"]] = False
            wound[d["agent"]] = 3
            action[d["agent"]] = None
            n_death += 1
            tick_events.append({"kind": "death", "agent": d["agent"], "name": d["name"],
                                "killed_by": d.get("killed_by", "")})
        elif ty == "reflection":
            ins = d.get("insights") or []
            if ins:
                tick_events.append({"kind": "reflection", "agent": d["agent"], "insight": ins[0]})
        elif ty == "director" and d.get("fired") and d.get("text"):
            tick_events.append({"kind": "world", "area": d.get("area", ""), "text": d["text"]})

    # 走位：每 tick 最多走 MOVE_SPEED 格，逐格記下來（回放要一格一格走）
    steps: dict[str, list[list[int]]] = {}
    for aid in AGENTS:
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
            if pos[aid] != cp_pos:
                drift += 1
                pos[aid] = cp_pos
                steps[aid] = [[cp_pos.x, cp_pos.y]]  # snap 到真值，走位以 checkpoint 為準

    agents_frame = {}
    for aid in AGENTS:
        cell = {"x": pos[aid].x, "y": pos[aid].y, "area": grid.area_at(pos[aid]),
                "wound": wound[aid], "alive": alive[aid]}
        if aid in steps:
            cell["steps"] = steps[aid]
        th = thought_at.get((t, aid))
        if th:
            cell["thought"] = th
        agents_frame[aid] = cell
    frames.append({"tick": t, "agents": agents_frame, "events": tick_events})

print(f"drift vs checkpoints: {drift} cell-mismatches (snapped)")

legend = {sym: {"name": n, "walk": w} for sym, (n, w) in jianghu.LEGEND.items()}
areas = [{"name": a.name, "x0": a.x0, "y0": a.y0, "x1": a.x1, "y1": a.y1, "desc": a.description}
         for a in grid.areas.values()]
cast = [{"id": aid, "name": AGENTS[aid]["name"], "skill": AGENTS[aid]["skill"],
         "home": AGENTS[aid]["home_area"], "kin": list(AGENTS[aid].get("kin", []))}
        for aid in AGENTS]

out = {
    "scenario": "jianghu",
    "title": jianghu.NAME and "衡山城 · 劉正風金盆洗手",
    "rows": jianghu.GRID_ROWS,
    "legend": legend,
    "areas": areas,
    "cast": cast,
    "street": grid.street,
    "frames": frames,
    "max_tick": max_tick,
    "stats": {"speeches": n_speech, "attacks": n_attack, "deaths": n_death,
              "walked": sum(walked.values())},
}

outp = Path(__file__).with_name("frames.json")
outp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print("wrote", outp, f"{outp.stat().st_size / 1024:.1f} KB")

# ---- 注入模板 ----
tpl = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
marker = "/*__DATA__*/ null"
if marker not in tpl:
    raise SystemExit(f"template.html 裡找不到注入點 {marker!r}")
html = tpl.replace(marker, json.dumps(out, ensure_ascii=False, separators=(",", ":")))
htmlp = ROOT / "jianghu_replay.html"
htmlp.write_text(html, encoding="utf-8")
print("wrote", htmlp, f"{htmlp.stat().st_size / 1024:.1f} KB")

print("\ndeaths:")
for f in frames:
    for e in f["events"]:
        if e["kind"] == "death":
            print(f"  t{f['tick']}: {e['name']} ← {e['killed_by']}")
print(f"speeches={n_speech} attacks={n_attack} deaths={n_death} steps={sum(walked.values())}")
