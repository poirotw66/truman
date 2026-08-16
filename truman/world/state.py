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
class Goal:
    """一個角色今天要做到的事，寫成世界判定得出來的形式。

    先前「目的」只躺在 persona 的最後一段散文裡（「今天你只要一件事：把這個手洗完」）。
    那對 LLM 有用，但對世界沒有用——引擎不知道誰達成了什麼，報表算不出達成率，
    回放頁也沒東西可標。這個 dataclass 就是把那句散文變成可判定的東西。

    判定一律是**純函式、不呼叫 LLM、只看世界狀態與當下 tick 的訊號**
    （見 `world/goals.py`）。理由和 `_resolve_attack` 綁死隨機源是同一個：
    replay 必須重現同一個結局，否則整條時間線對不上。

    status 只會單向前進 open → done / failed，結了案就不再翻盤。
    """

    kind: str  # 判定器名稱，見 goals.CHECKERS
    text: str  # 給角色自己看的一句話（進 system[1]）
    params: dict = field(default_factory=dict)
    status: str = "open"  # open / done / failed
    at_tick: int = -1  # 結案的 tick，-1 表示還沒結
    note: str = ""  # 結案理由，給報表與回放頁

    @property
    def open(self) -> bool:
        return self.status == "open"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "text": self.text,
            "params": self.params,
            "status": self.status,
            "at_tick": self.at_tick,
            "note": self.note,
        }

    @staticmethod
    def from_dict(d: dict) -> "Goal":
        return Goal(
            kind=d["kind"],
            text=d.get("text", ""),
            params=dict(d.get("params", {})),
            status=d.get("status", "open"),
            at_tick=d.get("at_tick", -1),
            note=d.get("note", ""),
        )


@dataclass
class Art:
    """一門配備在角色身上的絕技——這個世界裡的「工具」。

    這裡**只存會變的那一半**（還剩幾次、冷卻到哪一拍）。招式叫什麼、做什麼、
    什麼時候該用，全部在 `world/arts.py` 的目錄裡，用 id 查。

    這樣拆有兩個理由：checkpoint 不必把說明文字抄六份；改招式的說明文字
    不會讓舊存檔失效。這也剛好是 tool definition（靜態）與 tool state（動態）
    的分界——絕技就是這個世界的 tool。

    uses_left = -1 表示不限次數。ready_at 是「下一次可用的 tick」。
    """

    id: str
    uses_left: int = -1
    ready_at: int = -1
    used: int = 0  # 已經用過幾次，報表用

    def available(self, tick: int) -> tuple[bool, str]:
        """能不能用。回傳 (可以嗎, 不行的話是為什麼)。"""
        if self.uses_left == 0:
            return False, "used_up"
        if tick < self.ready_at:
            return False, "cooling"
        return True, ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "uses_left": self.uses_left,
            "ready_at": self.ready_at,
            "used": self.used,
        }

    @staticmethod
    def from_dict(d: dict) -> "Art":
        return Art(
            id=d["id"],
            uses_left=d.get("uses_left", -1),
            ready_at=d.get("ready_at", -1),
            used=d.get("used", 0),
        )


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
    # 這個人自己的模型設定：{"model":..., "temperature":..., "thinking":...}。
    # 空的就照 SimConfig 的分層路由走。可序列化，所以 checkpoint / fork 都帶得過去。
    llm: dict = field(default_factory=dict)
    # --- 目的與絕技 ---
    # 今天要做到的事。空的表示這個劇本沒給他目的（和平劇本多半如此），
    # 那麼世界不會判定任何東西，prompt 裡也不會出現這一段。
    goals: list[Goal] = field(default_factory=list)
    # 配備的絕技（＝這個角色手上的工具）。同樣是空的就整段不存在。
    arts: list[Art] = field(default_factory=list)
    # 絕技打出來的暫時效果：{效果名: {"amount": n, "until": tick}}。
    # 過期不清除也不影響判定（讀的時候比對 tick），但每 tick 掃一次比較好debug。
    buffs: dict = field(default_factory=dict)

    def art(self, art_id: str) -> "Art | None":
        for x in self.arts:
            if x.id == art_id:
                return x
        return None

    def buff(self, name: str, tick: int) -> int:
        """目前生效中的某個效果值。過期或沒有就是 0。"""
        b = self.buffs.get(name)
        if not b or tick > b.get("until", -1):
            return 0
        return int(b.get("amount", 0))

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
            "llm": self.llm,
            "goals": [g.to_dict() for g in self.goals],
            "arts": [x.to_dict() for x in self.arts],
            "buffs": self.buffs,
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
            llm=dict(d.get("llm", {})),
            # 舊 checkpoint 沒有這三個欄位——一律當成空的，舊 run 照樣讀得回來。
            goals=[Goal.from_dict(g) for g in d.get("goals", [])],
            arts=[Art.from_dict(x) for x in d.get("arts", [])],
            buffs=dict(d.get("buffs", {})),
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
    # 劇本級結局（嵐潮等）："" / held / partial / lost；文案給 demo／報表。
    outcome: str = ""
    outcome_text: str = ""
    # 已淹格子 [[x,y], ...]——跟 checkpoint 走，fork 後能把洪水覆寫回地圖。
    flooded: list = field(default_factory=list)

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
            "outcome": self.outcome,
            "outcome_text": self.outcome_text,
            "flooded": list(self.flooded),
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
            outcome=d.get("outcome", ""),
            outcome_text=d.get("outcome_text", ""),
            flooded=list(d.get("flooded", [])),
        )
