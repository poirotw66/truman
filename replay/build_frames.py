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
    replay/frames.json      中繼資料（可關）
    <out>                   自帶資料、離線可開的回放頁（預設 jianghu_replay.html）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scenarios import jianghu  # noqa: E402
from art.embed_portraits import portrait_map  # noqa: E402
from truman.world.grid import Pos  # noqa: E402

DEFAULT_SOURCES = [("j1", 0, 48), ("j1b", 48, 10**9)]
DEFAULT_CHECKPOINTS = [("j1", (12, 24, 36, 48)), ("j1b", (60, 72, 84, 96))]
MOVE_SPEED = 3


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

    # 接力的 run 之間 tick 是重疊的：前一段的界線 = 後一段的 fork 點。
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


def build_replay(
    runs: list[str] | None = None,
    out: Path | str | None = None,
    *,
    write_frames_json: bool = True,
    quiet: bool = False,
) -> Path:
    """從 runs 的 events.jsonl 建回放 HTML，回傳輸出路徑。"""
    sources, checkpoint_specs = resolve_sources(runs)
    out_path = Path(out) if out else ROOT / "jianghu_replay.html"
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    for run, lo, hi in sources:
        ev = ROOT / "runs" / run / "events.jsonl"
        if not ev.exists():
            raise FileNotFoundError(f"找不到 {ev}")
        if not quiet:
            print(f"  {run}: tick [{lo}, {'∞' if hi > 10**8 else hi})")

    grid = jianghu.build_grid()
    agents = {a["id"]: a for a in jianghu.AGENTS}
    start = {a["id"]: Pos(*a["start"]) for a in jianghu.AGENTS}

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
                    thought_at[(int(parts[0]), parts[1])] = th

    # checkpoint 檔名比它描述的狀態晚一拍。
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
    walked = {aid: 0 for aid in agents}

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
                tick_events.append(
                    {"kind": "interact", "agent": d["agent"], "obj": d.get("object", "")}
                )
            elif ty == "speech":
                action[d["speaker"]] = None
                n_speech += 1
                tick_events.append(
                    {
                        "kind": "speech",
                        "agent": d["speaker"],
                        "name": d["speaker_name"],
                        "to": d.get("to"),
                        "text": d["utterance"],
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
                        "line": d["line"],
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
                        "name": d["name"],
                        "killed_by": d.get("killed_by", ""),
                    }
                )
            elif ty == "reflection":
                ins = d.get("insights") or []
                if ins:
                    tick_events.append(
                        {"kind": "reflection", "agent": d["agent"], "insight": ins[0]}
                    )
            elif ty == "director" and d.get("fired") and d.get("text"):
                tick_events.append(
                    {"kind": "world", "area": d.get("area", ""), "text": d["text"]}
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
                if t < len(frames)
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

    legend = {sym: {"name": n, "walk": w} for sym, (n, w) in jianghu.LEGEND.items()}
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
    cast = [
        {
            "id": aid,
            "name": agents[aid]["name"],
            "skill": agents[aid]["skill"],
            "home": agents[aid]["home_area"],
            "kin": list(agents[aid].get("kin", [])),
        }
        for aid in agents
    ]

    payload = {
        "scenario": "jianghu",
        "title": jianghu.NAME and "衡山城 · 劉正風金盆洗手",
        "rows": jianghu.GRID_ROWS,
        "legend": legend,
        "areas": areas,
        "cast": cast,
        "street": grid.street,
        "frames": frames,
        "max_tick": max_tick,
        "stats": {
            "speeches": n_speech,
            "attacks": n_attack,
            "deaths": n_death,
            "walked": sum(walked.values()),
        },
        "portraits": portrait_map("jianghu"),
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
    ap.add_argument("--out", default="jianghu_replay.html", help="輸出的 HTML 檔名")
    args = ap.parse_args(argv)
    try:
        build_replay(args.run, args.out)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
