"""把一場 run 的事件日誌重建成回放用的逐 tick frames，並產出自帶資料的 HTML。

沒有 API 呼叫。座標優先讀日誌裡的 `snapshot` 事件——引擎每個 tick 記一次全員位置／
傷勢／生死，那是**真值**。舊的 run（snapshot 是後來才加的）才退回「把 move_to 意圖
丟回同一套 BFS 重放、再拿 checkpoint 對答案」的近似法。
其餘（心裡話／對話／動手／死亡／導演旁白）直接從事件日誌撈。

用法：
    python replay/build_frames.py                 # 預設 j1(0-47) + j1b(48-)
    python replay/build_frames.py --run j2        # 單一 run
    python replay/build_frames.py --run j2 --out j2_replay.html
輸出：
    replay/frames.json      中繼資料
    <out>                   自帶資料、離線可開的回放頁（預設 jianghu_replay.html）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scenarios import jianghu  # noqa: E402
from art.embed_portraits import portrait_map  # noqa: E402
from truman.world.grid import Pos  # noqa: E402

# (run 名稱, 取這個 run 的 tick 範圍 [起, 迄))：j1 跑了前半天，j1b 從 checkpoint fork 續跑
SOURCES = [("j1", 0, 48), ("j1b", 48, 10**9)]
CHECKPOINTS = [("j1", (12, 24, 36, 48)), ("j1b", (60, 72, 84, 96))]

_ap = argparse.ArgumentParser(description="事件日誌 → 回放頁")
_ap.add_argument("--run", action="append", help="改用這個 run（可重複，依序接起來）")
_ap.add_argument("--out", default="jianghu_replay.html", help="輸出的 HTML 檔名")
ARGS = _ap.parse_args()
if ARGS.run:
    SOURCES = [(ARGS.run[0], 0, 10**9)] if len(ARGS.run) == 1 else         [(r, 0, 10**9) for r in ARGS.run]
    # 單一 run 的 checkpoint 對答案：有幾個就撿幾個
    CHECKPOINTS = [(r, tuple(int(p.stem[1:]) for p in
                             sorted((ROOT / "runs" / r / "checkpoints").glob("t*.json"))))
                   for r, _, _ in SOURCES
                   if (ROOT / "runs" / r / "checkpoints").exists()]

MOVE_SPEED = 3
grid = jianghu.build_grid()


def walk_between(a: Pos, b: Pos, limit: int = 8) -> list[list[int]]:
    """兩個已知位置之間的最短路徑（不含起點）。

    snapshot 只記每個 tick 結束時站在哪，中間怎麼走沒記。回放要一格一格走才好看，
    所以這裡補一條最短路——反正兩端都是真值，中間只是視覺上的補間。
    """
    if a == b:
        return []
    from collections import deque

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
    return [[b.x, b.y]]                      # 不可達（例如被 snap 過）就直接跳過去

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

# 逐 tick 真值快照（引擎在每個 tick 結尾寫的）。舊 run 沒有這個事件，就退回 BFS 重建。
snaps: dict[int, dict] = {}
for r in events:
    if r["type"] == "snapshot":
        snaps[r["tick"]] = r["data"]["agents"]
USE_SNAPSHOT = len(snaps) > max_tick * 0.9
print(f"snapshot 事件：{len(snaps)} / {max_tick + 1} tick"
      f" → 位置來源：{'snapshot（真值）' if USE_SNAPSHOT else 'BFS 重建（近似，舊 run）'}")

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

    steps: dict[str, list[list[int]]] = {}
    if USE_SNAPSHOT and t in snaps:
        # 真值：位置／傷勢／生死全部照引擎當時記的來，中間補一條最短路讓人一格一格走
        for aid, snap in snaps[t].items():
            if aid not in AGENTS:
                continue
            nxt = Pos.of(snap["pos"])
            if nxt != pos[aid]:
                steps[aid] = walk_between(pos[aid], nxt)
                walked[aid] += len(steps[aid])
                pos[aid] = nxt
            wound[aid] = snap.get("wound", wound[aid])
            alive[aid] = snap.get("alive", alive[aid])
    else:
        # 舊 run：把 move_to 意圖丟回 BFS 重放，每 tick 最多走 MOVE_SPEED 格
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
                    steps[aid] = [[cp_pos.x, cp_pos.y]]  # snap 到真值

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

if USE_SNAPSHOT:
    # 有真值就順便拿 checkpoint 再對一次——兩邊都對得上才敢說這條時間線是準的
    bad = sum(1 for t, cp in checkpoints.items()
              for aid, cp_pos in cp.items()
              if t < len(frames) and Pos.of([frames[t]["agents"][aid]["x"],
                                             frames[t]["agents"][aid]["y"]]) != cp_pos)
    print(f"snapshot vs checkpoint：{bad} 處不一致" + ("（完全吻合）" if not bad else " ⚠"))
else:
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
    "portraits": portrait_map("jianghu"),
}

outp = Path(__file__).with_name("frames.json")
outp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print("wrote", outp, f"{outp.stat().st_size / 1024:.1f} KB")

# ---- 注入模板（共用像素美術 + 資料）----
tpl = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
for marker in ("/*__DATA__*/ null", "/*__PIXELART__*/"):
    if marker not in tpl:
        raise SystemExit(f"template.html 裡找不到注入點 {marker!r}")
art = (ROOT / "web" / "pixelart.js").read_text(encoding="utf-8")
html = tpl.replace("/*__PIXELART__*/", art)
html = html.replace("/*__DATA__*/ null", json.dumps(out, ensure_ascii=False, separators=(",", ":")))
htmlp = ROOT / ARGS.out
htmlp.write_text(html, encoding="utf-8")
print("wrote", htmlp, f"{htmlp.stat().st_size / 1024:.1f} KB")

print("\ndeaths:")
for f in frames:
    for e in f["events"]:
        if e["kind"] == "death":
            print(f"  t{f['tick']}: {e['name']} ← {e['killed_by']}")
print(f"speeches={n_speech} attacks={n_attack} deaths={n_death} steps={sum(walked.values())}")
