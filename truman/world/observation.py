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
    rejection: str = ""  # 上一步被世界駁回的理由，只出現一次
    wound: int = 0  # 自己的傷勢
    fury: int = 0  # 義憤：親眼見人被殺會漲，出手更狠（和平劇本永遠是 0）

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
                f"{v['name']}（在{v['area']}，{_state_word(v)}）" for v in self.visible
            )
            lines.append(f"你看得見：{who}。")
            # 世界區塊只寫了「三格以內聽得見」這條規則，但 agent 拿不到座標距離，
            # 沒辦法自己算。誰在射程內必須每個 tick 明講。
            in_earshot = [
                v["name"] for v in self.visible if v.get("hearable") and v.get("alive", True)
            ]
            if in_earshot:
                lines.append(f"其中聽得見你說話的只有：{'、'.join(in_earshot)}。")
            else:
                lines.append("他們都離你太遠，你現在說什麼都沒有人聽得見。")
        else:
            lines.append("附近沒有其他人。")

        if self.heard:
            lines.append("你聽見：")
            for h in self.heard:
                tag = "（對你說）" if h.get("to") == self.agent_id else ""
                lines.append(f"  {h['speaker_name']}{tag}：「{h['utterance']}」")

        for text in self.injected:
            lines.append(text)

        # 駁回理由要在眼前，不能只躺在記憶裡等檢索去撈。
        if self.rejection:
            lines.append(f"你上一步沒有做成：{self.rejection}")

        # 傷勢：要講**不對稱**，不能只講壞處。機制上傷只扣守勢、重傷反而讓攻擊更狠，
        # 但先前這裡只寫「使不上力／再挨一下就完了」，角色讀完的合理結論是「別打」——
        # 於是背水一戰整條規則從來沒被觸發過（j2 全程 13 次動手只有 1 次是帶傷者發動）。
        if self.wound == 1:
            lines.append(
                "你身上有傷。出手還使得上力，但防身已經不如平時——"
                "對方要傷你，比先前容易得多。"
            )
        elif self.wound:
            lines.append(
                "你傷得很重，站著都吃力，再挨一下就完了。"
                "但也正因為命都豁出去了，你這一刀會比平時更狠——"
                "只是門戶大開，對方若擋得住，你就沒有下一次。"
            )

        # 義憤：先前只進骰子、沒進眼前，角色根本不知道自己在憤怒
        # （儀琳 fury 封頂 4 到死都沒動過手）。
        if self.fury >= 4:
            lines.append(
                "你已經見過不只一條人命在你眼前沒了。這股火燒得你手都在抖，"
                "真動起手來，你不會再留餘地。"
            )
        elif self.fury:
            lines.append(
                "你親眼見過有人在你面前被殺。那一幕壓在心口散不掉，"
                "真動起手來，你會比平時狠。"
            )

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


def _state_word(v: dict) -> str:
    """看得見的人長什麼樣。沒有傷、沒有屍體時完全不提，和平劇本讀起來一模一樣。"""
    if not v.get("alive", True):
        return "倒在地上，已經沒氣了"
    wound = v.get("wound", 0)
    hurt = ("", "帶著傷", "傷得很重")[min(wound, 2)]
    doing = f"看起來正在{v['doing']}"
    return f"{hurt}，{doing}" if hurt else doing


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
            dist = a.pos.chebyshev(other.pos)
            if dist <= cfg.vision_radius:
                visible.append(
                    {
                        "id": oid,
                        "name": other.name,
                        "area": grid.area_at(other.pos),
                        "doing": describe_action(other),
                        # 看得見 ≠ 喊得到（vision 5 > hearing 3）。不標出來的話 agent 會
                        # 對著看得見卻聽不見的人講話，每次都被 _apply_intent 駁回，
                        # 白燒一整趟呼叫——g4 有 13% 的 intent 是這樣掉的。
                        # 只從 visible 推導：hearing > vision 時寧可少給機會，
                        # 也不要洩漏一個看不見的人的存在。
                        "hearable": dist <= cfg.hearing_radius,
                        "wound": other.wound,
                        "alive": other.alive,
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
            rejection=a.last_rejection,
            wound=a.wound,
            fury=a.fury,
        )
    return obs
