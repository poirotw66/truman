"""目的判定：把「這個人今天要做到什麼」變成世界算得出來的東西。

一條鐵律：**判定不呼叫 LLM，只看世界狀態與這一拍的訊號。**
理由和 `_resolve_attack` 把隨機源綁死在 (seed, tick, 攻, 守) 上一樣——
replay 必須重現同一個結局。用 LLM 當裁判的話，同一份日誌重放兩次會給出
不同的達成率，那報表就沒有意義了。

判定器都是純函式，簽名一致：

    check(world, grid, cfg, agent, goal, sig) -> (status, note) | None

回傳 None 表示「還沒結案」。回傳 ("done"|"failed", 理由) 就結案，不再翻盤。

## 主動目的與被動目的

有些目的是「要做到某件事」（洗完手、走到城門、把人帶到僻靜處），
有些是「不要讓某件事發生」（活下來、別讓她死、別被認出來）。
跑到收工還沒結案時，這兩種的預設結局是相反的：

    主動（reach / ritual / isolate / meet / prevent）  沒做到就是失敗
    被動（survive / protect / conceal）                沒出事就是達成

`PASSIVE` 這個集合就是在記這件事。

## 死了怎麼算

人死了，所有還沒結案的目的一律失敗。「他雖然死了但沒被認出來」這種算法
在敘事上講得通，但會讓報表出現「死人達成了目的」這種讀不懂的數字。
簡單、一致、講得清楚，比精確重要。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 跑到最後還沒結案時，預設算達成的那幾種
PASSIVE = frozenset({"survive", "protect", "conceal"})


@dataclass
class Signals:
    """這一拍發生了什麼，判定器需要但世界狀態上看不出來的部分。

    只放「事件性」的東西。位置、傷勢、生死這些看 world 就好，不必重複帶。
    """

    tick: int
    rites: dict[str, list[str]] = field(default_factory=dict)  # aid -> 完成的儀式名
    exposures: list[dict] = field(default_factory=list)  # 當眾指證，見 arts.denounce
    deaths: list[str] = field(default_factory=list)  # 這一拍死掉的人
    # 這一拍已經出手過的人——同一拍只能動手一次，否則 act 打傷、reply 補刀
    # 會把對決壓成一瞬（j7 劉正風同拍兩刀結果費彬）。
    attacked: set[str] = field(default_factory=set)

    @staticmethod
    def empty(tick: int) -> "Signals":
        return Signals(tick=tick)


# ------------------------------------------------------------------ 判定器


def _check_survive(world, grid, cfg, a, goal, sig):
    # 活著就繼續開著；死掉由外層的統一規則接手。
    return None


def _check_reach(world, grid, cfg, a, goal, sig):
    want = goal.params.get("area", "")
    if grid.area_at(a.pos) == want:
        return "done", f"走到了{want}"
    return None


def _check_ritual(world, grid, cfg, a, goal, sig):
    rite = goal.params.get("rite", "")
    if rite and rite in sig.rites.get(a.id, []):
        return "done", f"{rite}辦成了"
    by = goal.params.get("by_tick")
    if by is not None and sig.tick > by:
        return "failed", f"過了時辰，{rite}沒有辦成"
    return None


def _check_protect(world, grid, cfg, a, goal, sig):
    for who in goal.params.get("who", []):
        other = world.agents.get(who)
        if other is not None and not other.alive:
            return "failed", f"{other.name}死了"
    return None


def _check_prevent(world, grid, cfg, a, goal, sig):
    other = world.agents.get(goal.params.get("agent", ""))
    if other is None:
        return None
    idx = int(goal.params.get("goal", 0))
    if idx >= len(other.goals):
        return None
    target_goal = other.goals[idx]
    if target_goal.status == "failed":
        return "done", f"{other.name}沒能做成他要做的事"
    if target_goal.status == "done":
        return "failed", f"{other.name}還是做成了"
    # on_cast：對手一動手做對應儀式就算你沒攔住——不必等焊完／禮成。
    if goal.params.get("on_cast"):
        act = other.action or {}
        if (
            act.get("kind") == "cast_rite"
            and not act.get("done")
            and target_goal.kind == "ritual"
            and act.get("rite") == target_goal.params.get("rite")
        ):
            return "failed", f"{other.name}已經動手開始{act.get('rite')}了"
    return None


def _check_isolate(world, grid, cfg, a, goal, sig):
    other = world.agents.get(goal.params.get("target", ""))
    if other is None or not other.alive or not a.alive:
        return None
    areas = goal.params.get("areas") or []
    here = grid.area_at(a.pos)
    if here != grid.area_at(other.pos) or (areas and here not in areas):
        return None
    # 「沒有別人看見」才算數——當著滿場的人把人帶走不叫帶走。
    for oid, o in world.agents.items():
        if oid in (a.id, other.id) or not o.alive:
            continue
        if o.pos.chebyshev(a.pos) <= cfg.vision_radius:
            return None
    return "done", f"在{here}和{other.name}單獨相處"


def _check_conceal(world, grid, cfg, a, goal, sig):
    for ex in sig.exposures:
        if ex.get("target") == a.id and ex.get("landed"):
            by = world.agents.get(ex.get("by", ""))
            who = by.name if by else "有人"
            return "failed", f"被{who}當眾說破了"
    return None


def _check_meet(world, grid, cfg, a, goal, sig):
    other = world.agents.get(goal.params.get("who", ""))
    if other is None or not other.alive or not a.alive:
        return None
    if other.pos.chebyshev(a.pos) <= cfg.hearing_radius:
        return "done", f"見到了{other.name}"
    return None


CHECKERS = {
    "survive": _check_survive,
    "reach": _check_reach,
    "ritual": _check_ritual,
    "protect": _check_protect,
    "prevent": _check_prevent,
    "isolate": _check_isolate,
    "conceal": _check_conceal,
    "meet": _check_meet,
}


# ------------------------------------------------------------------ 驅動


def evaluate(world, grid, cfg, sig: Signals, log=None) -> list[dict]:
    """跑一輪判定，就地更新 goal.status。回傳這一拍結案的目的（給日誌與報表）。

    `prevent` 讀的是別人的目的狀態，所以要等其他目的都算完才算它，
    否則它會慢一拍才看到對手成功或失敗。
    """
    closed: list[dict] = []

    def close(a, goal, idx, status, note):
        goal.status = status
        goal.at_tick = sig.tick
        goal.note = note
        rec = {
            "agent": a.id, "name": a.name, "goal": idx, "kind": goal.kind,
            "text": goal.text, "status": status, "note": note, "tick": sig.tick,
        }
        closed.append(rec)
        if log is not None:
            log.write("goal_done" if status == "done" else "goal_failed", rec)

    for pas in (False, True):  # 先算一般的，再算 prevent
        for a in world.agents.values():
            for idx, goal in enumerate(a.goals):
                if not goal.open:
                    continue
                if (goal.kind == "prevent") != pas:
                    continue
                # 人不在了，剩下的都不必算了。
                if not a.alive:
                    close(a, goal, idx, "failed", "人已經不在了")
                    continue
                fn = CHECKERS.get(goal.kind)
                if fn is None:
                    continue
                out = fn(world, grid, cfg, a, goal, sig)
                if out:
                    close(a, goal, idx, out[0], out[1])
    return closed


def finalize(world, grid, cfg, tick: int, log=None) -> list[dict]:
    """收工結算：還開著的目的照主動／被動的預設結局收掉。

    `prevent` 一樣要留到最後：它讀的是別人的目的狀態，而那些目的可能正是在這一輪
    才被收掉的。少了這個順序，「劉正風沒洗成手」和「費彬沒攔住他」會同時成立——
    報表上兩個人都失敗，讀的人只會覺得這張表壞了。
    """
    closed: list[dict] = []

    def close(a, goal, idx, status, note):
        goal.status = status
        goal.at_tick = tick
        goal.note = note
        rec = {
            "agent": a.id, "name": a.name, "goal": idx, "kind": goal.kind,
            "text": goal.text, "status": status, "note": note, "tick": tick,
        }
        closed.append(rec)
        if log is not None:
            log.write("goal_done" if status == "done" else "goal_failed", rec)

    for pas in (False, True):  # 先收一般的，再收 prevent
        for a in world.agents.values():
            for idx, goal in enumerate(a.goals):
                if not goal.open or (goal.kind == "prevent") != pas:
                    continue
                if not a.alive:
                    close(a, goal, idx, "failed", "人已經不在了")
                    continue
                if pas:
                    # 對手的目的這時候都已經結案了，判定器現在讀得到真正的結果。
                    out = _check_prevent(world, grid, cfg, a, goal,
                                         Signals.empty(tick))
                    if out:
                        close(a, goal, idx, out[0], out[1])
                        continue
                if goal.kind in PASSIVE:
                    close(a, goal, idx, "done", "撐到了最後")
                else:
                    close(a, goal, idx, "failed", "到收工都沒有做成")
    return closed


def progress_line(a) -> str:
    """給 observation 用的一句話：目的現在怎麼樣了。"""
    if not a.goals:
        return ""
    bits = []
    for g in a.goals:
        mark = {"open": "還沒做到", "done": "已經做到", "failed": "已經沒指望了"}[g.status]
        bits.append(f"{g.text}（{mark}）")
    return "；".join(bits)


def unknown_kinds(goals) -> list[str]:
    """設定檔驗證用：回傳不認得的判定器名稱。"""
    return [g.get("kind", "") for g in goals if g.get("kind") not in CHECKERS]
