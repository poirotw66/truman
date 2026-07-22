"""導演層 —— 楚門機制的核心。

導演不改世界的權威狀態，只改「誰能觀察到什麼」以及「演員收到什麼指示」。
這個區分很重要：世界引擎仍然是唯一權威，所以分支重跑時一切仍可重現。

四種操縱：
  inject     只有某一個人會觀察到的「事實」（主角的巧合都是從這裡來的）
  broadcast  某區域內所有人共同觀察到的事件
  summon     把某個演員調度到某個地點（製造巧遇）
  cue        給演員的場記指示，混在他自己的觀察裡，只有他看得見
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..world.grid import Grid
from ..world.state import WorldState


@dataclass
class Director:
    script: list[dict] = field(default_factory=list)
    log: object | None = None
    runtime_injections: list[dict] = field(default_factory=list)

    def add_runtime(self, agent_id: str, text: str, tick: int) -> None:
        """CLI / 分支時臨時注入。"""
        self.runtime_injections.append(
            {"tick": tick, "kind": "inject", "agent": agent_id, "text": text}
        )

    def cues_for_tick(self, tick: int) -> list[dict]:
        return [c for c in self.script + self.runtime_injections if c["tick"] == tick]

    def apply(self, world: WorldState, grid: Grid) -> dict[str, list[str]]:
        """回傳 {agent_id: [要塞進 observation 的文字, ...]}。"""
        injections: dict[str, list[str]] = {}
        for cue in self.cues_for_tick(world.tick):
            kind = cue["kind"]

            if kind == "inject":
                injections.setdefault(cue["agent"], []).append(cue["text"])

            elif kind == "broadcast":
                area = cue.get("area")
                for aid, a in world.agents.items():
                    if area is None or grid.area_at(a.pos) == area:
                        injections.setdefault(aid, []).append(cue["text"])

            elif kind == "summon":
                a = world.agents.get(cue["agent"])
                target = grid.resolve_area(cue.get("area", ""))
                if a is not None and target:
                    a.action = {
                        "kind": "move_to",
                        "target_area": target,
                        "target_agent": "",
                        "utterance": "",
                        "object": "",
                        "path": [p.as_list() for p in grid.path(a.pos, target)],
                        "ticks_left": 0,
                        "source": "director",
                    }
                    a.plan = cue.get("plan", a.plan)

            elif kind == "cue":
                # 場記指示偽裝成演員自己的念頭 —— 主角永遠拿不到這種東西。
                injections.setdefault(cue["agent"], []).append(
                    f"（你想起製作組交代過：{cue['text']}）"
                )

            if self.log:
                self.log.write("director", {**cue, "fired": True})
            world.director_fired.append(world.tick)

        return injections
