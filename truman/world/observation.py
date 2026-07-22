"""Observation：世界狀態的「每 agent 過濾投影」。

這一層是楚門機制的掛載點 —— 導演的所有操縱都是對這裡動手腳，
而不是去改世界的權威狀態。agent 永遠只能透過 Observation 認識世界。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import clock_str
from .grid import Grid
from .state import AgentState, WorldState


@dataclass
class Observation:
    agent_id: str
    tick: int
    when: str
    area: str
    area_desc: str
    pos: str
    visible: list[dict] = field(default_factory=list)  # 看得見的人
    heard: list[dict] = field(default_factory=list)  # 這個 tick 聽見的話
    injected: list[str] = field(default_factory=list)  # 導演注入的「事實」
    doing: str = "沒有正在進行的事。"
    plan: str = ""

    # -------------------------------------------------------- 顯著度
    def new_faces(self, previously_seen: list[str]) -> list[str]:
        prev = set(previously_seen)
        return [v["name"] for v in self.visible if v["name"] not in prev]

    def addressed_to_me(self) -> list[dict]:
        return [h for h in self.heard if h.get("to") == self.agent_id]

    # -------------------------------------------------------- 渲染
    def render(self) -> str:
        lines = [
            f"現在是 {self.when}。",
            f"你在「{self.area}」，座標 {self.pos}。{self.area_desc}",
        ]
        if self.visible:
            who = "、".join(
                f"{v['name']}（在{v['area']}，看起來正在{v['doing']}）" for v in self.visible
            )
            lines.append(f"你看得見：{who}。")
        else:
            lines.append("附近沒有其他人。")

        if self.heard:
            lines.append("你聽見：")
            for h in self.heard:
                tag = "（對你說）" if h.get("to") == self.agent_id else ""
                lines.append(f"  {h['speaker_name']}{tag}：「{h['utterance']}」")

        for text in self.injected:
            lines.append(text)

        lines.append(f"你正在做的事：{self.doing}")
        lines.append(f"你原本的打算：{self.plan}")
        return "\n".join(lines)

    def retrieval_query(self) -> str:
        """用來檢索記憶的查詢字串。"""
        parts = [self.area, self.plan]
        parts += [v["name"] for v in self.visible]
        parts += [h["utterance"] for h in self.heard]
        parts += self.injected
        return " ".join(p for p in parts if p)


def describe_action(a: AgentState) -> str:
    act = a.action
    if not act:
        return "站著"
    kind = act.get("kind")
    if kind == "move_to":
        return f"往{act.get('target_area')}走"
    if kind == "interact":
        return f"{act.get('object') or '做些什麼'}"
    if kind == "speak":
        return "說話"
    return "待著"


def build_observations(
    world: WorldState,
    grid: Grid,
    speech_events: list[dict],
    injections: dict[str, list[str]],
    cfg,
) -> dict[str, Observation]:
    """一次替所有 agent 建 observation。speech_events 是上一個 tick 產生的發言。"""
    obs: dict[str, Observation] = {}
    when = clock_str(world.tick)

    for aid, a in world.agents.items():
        area = grid.area_at(a.pos)
        area_obj = grid.area(area)

        visible = []
        for oid, other in world.agents.items():
            if oid == aid:
                continue
            if a.pos.chebyshev(other.pos) <= cfg.vision_radius:
                visible.append(
                    {
                        "id": oid,
                        "name": other.name,
                        "area": grid.area_at(other.pos),
                        "doing": describe_action(other),
                    }
                )

        heard = []
        for ev in speech_events:
            if ev["speaker"] == aid:
                continue
            # 已經在同 tick 的對話追加輪裡回應過這句的人，不再收第二次
            if aid in ev.get("consumed_by", ()):
                continue
            speaker = world.agents.get(ev["speaker"])
            if speaker is None:
                continue
            if a.pos.chebyshev(speaker.pos) <= cfg.hearing_radius:
                heard.append(ev)

        obs[aid] = Observation(
            agent_id=aid,
            tick=world.tick,
            when=when,
            area=area,
            area_desc=area_obj.description if area_obj else "戶外的通道。",
            pos=str(a.pos),
            visible=visible,
            heard=heard,
            injected=injections.get(aid, []),
            doing=describe_action(a),
            plan=a.plan,
        )
    return obs
