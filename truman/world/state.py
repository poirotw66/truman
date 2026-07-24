"""世界狀態：完全可序列化，因此可 checkpoint、可分支、可 replay。

分支（counterfactual fork）是這個專案唯一能做因果推論的手段，
所以狀態結構從第一天就必須是可快照的 —— 不要在這裡塞不可序列化的東西。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..agents.memory import MemoryStream
from .grid import Pos


@dataclass
class AgentState:
    id: str
    name: str
    role: str  # "protagonist" | "actor"
    persona: str  # 穩定的自我描述（進快取區塊）
    home_area: str
    pos: Pos
    plan: str = "還沒想好今天要做什麼。"
    action: dict | None = None  # 進行中的動作
    memory: MemoryStream = field(default_factory=MemoryStream)
    seen_last_tick: list[str] = field(default_factory=list)
    last_think_tick: int = -999
    think_count: int = 0
    # 上一步被世界駁回的理由。只寫進記憶不夠——檢索不一定撈得到它，
    # g6 裡林淑就連續五個 tick 對著一個聽不見的人講同一件事。
    # 這一句會直接掛進下一個 tick 的 observation，看過一次就清掉。
    last_rejection: str = ""
    # --- 武林劇本才用得到 ---
    skill: int = 5  # 武功高低 1–10，只有世界引擎看得到，不寫進人設
    wound: int = 0  # 0 無傷 / 1 輕傷 / 2 重傷 / 3 死
    killed_by: str = ""  # 誰下的手（空字串表示還活著或不是死於人手）
    # 義憤：親眼見人被殺會被激起，出手更狠。和 skill 一樣是世界的屬性、不寫進人設，
    # 存在的目的是讓連續擊殺越來越難——見 Engine._resolve_attack。
    fury: int = 0
    # 這個人在意的人（師門／親友／知音）的 id。他們死了，噩耗會傳到這個人眼前，
    # 逼他面對「要不要討公道」——見 Engine._notify_kin。江湖上沒有白死的人。
    kin: list[str] = field(default_factory=list)

    @property
    def is_protagonist(self) -> bool:
        return self.role == "protagonist"

    @property
    def alive(self) -> bool:
        return self.wound < 3

    @property
    def wound_word(self) -> str:
        return ("無傷", "帶傷", "重傷", "已死")[min(self.wound, 3)]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "persona": self.persona,
            "home_area": self.home_area,
            "pos": self.pos.as_list(),
            "plan": self.plan,
            "action": self.action,
            "memory": self.memory.to_dict(),
            "seen_last_tick": self.seen_last_tick,
            "last_think_tick": self.last_think_tick,
            "think_count": self.think_count,
            "last_rejection": self.last_rejection,
            "skill": self.skill,
            "wound": self.wound,
            "killed_by": self.killed_by,
            "fury": self.fury,
            "kin": self.kin,
        }

    @staticmethod
    def from_dict(d: dict) -> "AgentState":
        return AgentState(
            id=d["id"],
            name=d["name"],
            role=d["role"],
            persona=d["persona"],
            home_area=d["home_area"],
            pos=Pos.of(d["pos"]),
            plan=d.get("plan", ""),
            action=d.get("action"),
            memory=MemoryStream.from_dict(d.get("memory", {})),
            seen_last_tick=list(d.get("seen_last_tick", [])),
            last_think_tick=d.get("last_think_tick", -999),
            think_count=d.get("think_count", 0),
            last_rejection=d.get("last_rejection", ""),
            skill=d.get("skill", 5),
            wound=d.get("wound", 0),
            killed_by=d.get("killed_by", ""),
            fury=d.get("fury", 0),
            kin=list(d.get("kin", [])),
        )


@dataclass
class WorldState:
    run_id: str
    scenario: str
    seed: int
    tick: int = 0
    agents: dict[str, AgentState] = field(default_factory=dict)
    # 導演層的執行紀錄
    director_fired: list[int] = field(default_factory=list)
    awareness_score: float = 0.0
    awareness_log: list[dict] = field(default_factory=list)

    def protagonist(self) -> AgentState:
        p = self.protagonist_or_none()
        if p is None:
            raise ValueError("這個劇本沒有主角")
        return p

    def protagonist_or_none(self) -> "AgentState | None":
        """箱庭劇本（hakoniwa）裡每個人都是普通村民，沒有主角是合法狀態。"""
        for a in self.agents.values():
            if a.is_protagonist:
                return a
        return None

    def occupants(self) -> dict[Pos, str]:
        return {a.pos: a.name[0] for a in self.agents.values()}

    def rng(self) -> random.Random:
        """每 tick 決定性的 RNG —— 同 seed + 同 tick 一定產生同樣的隨機序列。"""
        return random.Random(self.seed * 1_000_003 + self.tick)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "tick": self.tick,
            "agents": {k: v.to_dict() for k, v in self.agents.items()},
            "director_fired": self.director_fired,
            "awareness_score": self.awareness_score,
            "awareness_log": self.awareness_log,
        }

    @staticmethod
    def from_dict(d: dict) -> "WorldState":
        return WorldState(
            run_id=d["run_id"],
            scenario=d["scenario"],
            seed=d["seed"],
            tick=d["tick"],
            agents={k: AgentState.from_dict(v) for k, v in d["agents"].items()},
            director_fired=list(d.get("director_fired", [])),
            awareness_score=d.get("awareness_score", 0.0),
            awareness_log=list(d.get("awareness_log", [])),
        )
