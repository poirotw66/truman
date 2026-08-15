"""世界引擎：tick 迴圈與 intent 驗證。

兩個不能妥協的原則：

1. **agent 不能直接改世界狀態。** 它們只提交 intent，由這裡驗證後才生效。
   驗證失敗會把錯誤寫回它的記憶——不然 agent 會一直幻覺出不存在的地點。
2. **離散 tick + action queue。** lockstep 好 debug、好 replay，而且對 prompt cache
   友善。非同步即時留到之後再說。

對話用「同 tick 追加一輪」處理：tick t 說的話，被指名的人在同一個 t 內回一句，
其餘聽見的人在 t+1 才反應。這樣一次交談讀起來自然，成本又有上限。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from ..agents import cognition
from ..config import clock_str
from ..director import awareness
from ..obs import checkpoint
from . import arts as arts_mod
from . import goals as goals_mod
from .grid import Grid, Pos
from .observation import build_observations
from .state import WorldState


@dataclass
class Engine:
    world: WorldState
    grid: Grid
    cfg: object
    llm: object
    director: object
    log: object
    world_block_text: str
    run_dir: Path
    console: object | None = None
    pending_speech: list[dict] = field(default_factory=list)
    # 呼叫成敗計數。沒有這個，一次全滅的 run 也會安靜地印出漂亮的成本表。
    ok_calls: int = 0
    failed_calls: int = 0
    last_error: str = ""
    # 全軍覆沒時由 _check_fail_fast 立起來，外層的 tick 迴圈看到就收工。
    # 用旗標不用例外：兩個驅動迴圈（CLI 與 demo）都有 finally 在存檔與寫 run_summary，
    # 例外會讓那段在「已經決定放棄」的情況下還是照跑一遍，讀日誌的人會更困惑。
    aborted: bool = False
    abort_reason: str = ""
    _last_judge_tick: int = -999  # 收工強制評審用來避免重複評
    # 這一拍發生的、目的判定看得到但世界狀態上看不出來的事（儀式、指證、死亡）。
    # 每 tick 開頭重置，tick 結束前交給 goals.evaluate。
    _signals: goals_mod.Signals = field(default_factory=lambda: goals_mod.Signals.empty(0))

    # ------------------------------------------------------------ 主迴圈
    async def run(self, ticks: int) -> None:
        for _ in range(ticks):
            await self.tick()
        await self.finish()

    async def finish(self) -> None:
        """收工強制評一次覺察。

        judge 掛在 tick % judge_interval 上，而跑 N tick 走的是 tick 0..N-1
        ——tick N 不存在，所以最後一段軌跡永遠拿不到分數（g5 跑 48 tick 只評到
        tick 24 那一次）。CLI 有自己的 tick 迴圈，不走 run()，所以那邊也要呼叫。
        """
        await self._awareness_phase(force=True)
        # 收工結算：還開著的目的照主動／被動的預設結局收掉，報表才有完整的達成率。
        goals_mod.finalize(self.world, self.grid, self.cfg, self.world.tick, self.log)

    async def tick(self) -> None:
        w, t = self.world, self.world.tick
        self.log.write("tick_start", {"tick": t, "when": clock_str(t)})
        self._signals = goals_mod.Signals.empty(t)

        injections = self.director.apply(w, self.grid)
        obs = build_observations(w, self.grid, self.pending_speech, injections, self.cfg)

        # 死人不知覺、不決策、不移動。屍體仍然留在原地被別人看見。
        living = {aid: a for aid, a in w.agents.items() if a.alive}

        for aid, a in living.items():
            cognition.record_perception(a, obs[aid], self.cfg, t)

        # --- 主決策階段 ---
        thinkers: list[tuple[str, str]] = []
        for aid, a in living.items():
            need, reason = cognition.needs_llm(a, obs[aid], self.cfg, t)
            if need:
                thinkers.append((aid, reason))
            else:
                self.log.write("coast", {"agent": aid, "reason": reason})

        speech = await self._decide(thinkers, obs, suffix="act")

        # --- 對話追加輪：被指名的人在同一個 tick 內回應 ---
        # 每個人的追加輪只看得到「指名他」的那幾句；其餘他聽見的話照常在 t+1 送達。
        # 消化標記必須逐 (事件, 對象) 記，不能整批標記——否則會把別人沒聽見的話也吞掉。
        burst = self._burst_targets(speech)
        if burst:
            burst_obs = {}
            for aid, addressed in burst:
                per = build_observations(w, self.grid, addressed, {}, self.cfg)
                burst_obs[aid] = per[aid]
                cognition.record_perception(w.agents[aid], per[aid], self.cfg, t)
                for ev in addressed:
                    ev.setdefault("consumed_by", []).append(aid)
            speech += await self._decide(
                [(aid, "spoken_to") for aid, _ in burst], burst_obs, suffix="reply"
            )

        # --- 推進所有進行中的動作 ---
        for a in w.agents.values():
            if a.alive:
                self._advance(a)

        # 過期的絕技效果清掉。讀的時候本來就會比對 tick，清不清都不影響判定，
        # 但留著會讓 checkpoint 和日誌難讀——分不出「還在生效」和「早就過了」。
        for a in w.agents.values():
            for name in [k for k, v in a.buffs.items() if t > v.get("until", -1)]:
                a.buffs.pop(name)

        # 目的判定要在動作推進之後、快照之前：這一拍走到城門的人，這一拍就算數。
        for closed in goals_mod.evaluate(w, self.grid, self.cfg, self._signals, self.log):
            self._on_goal_closed(closed)

        await self._reflect_phase()
        await self._awareness_phase()

        # 逐 tick 全員位置＋傷勢快照。checkpoint 每 12 tick 才存一次，畫不出走位；
        # 這一行讓「回放畫面」能一格一格重現誰站哪、誰帶傷、誰倒下。一行 JSON，可忽略。
        self.log.write("snapshot", {
            "agents": {
                aid: {
                    "pos": ag.pos.as_list(), "area": self.grid.area_at(ag.pos),
                    "wound": ag.wound, "fury": ag.fury, "alive": ag.alive,
                }
                for aid, ag in w.agents.items()
            }
        })

        self.pending_speech = speech
        w.tick += 1
        if w.tick % self.cfg.checkpoint_interval == 0:
            path = checkpoint.save(w, self.run_dir)
            self.log.write("checkpoint", {"path": str(path)})

    # ------------------------------------------------------------ 決策
    async def _decide(self, who: list[tuple[str, str]], obs, suffix: str) -> list[dict]:
        if not who:
            return []
        w, t = self.world, self.world.tick

        calls = []
        for aid, reason in who:
            a = w.agents[aid]
            tier = cognition.pick_tier(a, reason, obs[aid], self.cfg)
            c = cognition.action_call(a, obs[aid], self.world_block_text, self.cfg, tier, t)
            c.key = f"{t}:{aid}:{suffix}"
            calls.append(c)
            # 駁回理由已經渲染進這次的 observation 了，看過就清掉——
            # 只在真的送進 prompt 時清，coast 的人下次還看得到。
            a.last_rejection = ""
            self.log.write("think", {"agent": aid, "reason": reason, "tier": tier})

        results = await self.llm.run_batch(calls)

        speech: list[dict] = []
        for aid, reason in who:
            a = w.agents[aid]
            res = results.get(f"{t}:{aid}:{suffix}")
            if isinstance(res, Exception) or res is None:
                self.failed_calls += 1
                self.last_error = str(res)
                self.log.write("think_failed", {"agent": aid, "error": str(res)})
                a.action = {"kind": "wait", "ticks_left": 1, "done": False}
                self._check_fail_fast()
                continue

            self.ok_calls += 1
            a.last_think_tick = t
            a.think_count += 1
            a.plan = (res.get("plan") or a.plan).strip()
            act = res.get("action")
            if not isinstance(act, dict):
                # schema 要求 object；偶發模型回字串會讓整拍崩潰（j7）。
                self.failed_calls += 1
                self.ok_calls -= 1
                self.last_error = f"{t}:{aid}:{suffix}: action 不是物件：{act!r}"
                self.log.write("think_failed", {"agent": aid, "error": self.last_error})
                a.action = {"kind": "wait", "ticks_left": 1, "done": False}
                self._check_fail_fast()
                continue
            cognition.record_decision(a, res, t)

            ev = self._apply_intent(a, act)
            if ev:
                speech.append(ev)

            if a.is_protagonist:
                awareness.score_tick(
                    w,
                    a,
                    res.get("thought", ""),
                    act.get("utterance", ""),
                    self.cfg,
                    self.log,
                )
            if self.console:
                self._echo(a, res)
        return speech

    def _check_fail_fast(self) -> None:
        """一次都沒成功過、又已經失敗這麼多次，就別再跑下去了。

        條件刻意寫成「從頭到尾一次都沒成功」而不是「連續失敗 N 次」：
        跑到一半遇到一段壞天氣（429、5xx）是暫時的，重試那層會處理，
        不該把一個已經跑出東西的 run 砍掉。真正要擋的是 j3 那種——
        設定就是錯的，每一次呼叫都會失敗，跑完 96 拍也只是把同一個錯誤重複 576 次。
        """
        cap = getattr(self.cfg, "abort_after_failures", 0)
        if not cap or self.aborted or self.ok_calls:
            return
        if self.failed_calls < cap:
            return
        self.aborted = True
        self.abort_reason = (
            f"連續 {self.failed_calls} 次呼叫全部失敗，一次都沒有成功過——"
            f"這通常是設定錯了（模型 ID、thinking_level、憑證），不是運氣不好。"
            f"最後一個錯誤：{self.last_error[:300]}"
        )
        self.log.write("run_aborted", {
            "reason": "all_calls_failing",
            "failed_calls": self.failed_calls,
            "last_error": self.last_error,
        })
        if self.console:
            self.console.print(f"\n[bold red]✕ 中止：{self.abort_reason}[/bold red]")

    def _burst_targets(self, speech: list[dict]) -> list[tuple[str, list[dict]]]:
        """回傳 [(被指名的人, 指名他的那幾句)]。

        排除：已經在這輪開過口的人（避免自問自答）、聽不見的人。
        """
        w = self.world
        spoke = {ev["speaker"] for ev in speech}
        by_target: dict[str, list[dict]] = {}
        for ev in speech:
            tgt = ev.get("to")
            if not tgt or tgt in spoke or tgt not in w.agents:
                continue
            if not w.agents[tgt].alive:  # 死人不接話
                continue
            speaker = w.agents[ev["speaker"]]
            if w.agents[tgt].pos.chebyshev(speaker.pos) > self.cfg.hearing_radius:
                continue
            by_target.setdefault(tgt, []).append(ev)
        return list(by_target.items())

    # ------------------------------------------------------------ intent 驗證
    def _apply_intent(self, a, act: dict) -> dict | None:
        """驗證並套用。回傳一個 speech event（如果有的話）。"""
        w, t, when = self.world, self.world.tick, clock_str(self.world.tick)
        kind = (act.get("kind") or "wait").strip()

        def reject(msg: str, **detail):
            self.log.write(
                "invalid_intent",
                {"agent": a.id, "action": act, "reason": msg, **detail},
            )
            # 把錯誤寫回記憶，否則它會一直重複同一個幻覺。
            a.memory.add(t, when, "observation", msg, importance=5)
            # 記憶不保證被檢索到，所以同一句也直接掛進下一個 tick 的 observation。
            a.last_rejection = msg
            a.action = {"kind": "wait", "ticks_left": 1, "done": False}
            return None

        if kind == "move_to":
            target = self.grid.resolve_area(act.get("target_area", ""))
            if target is None:
                return reject(f"我想去「{act.get('target_area')}」，但這座鎮上沒有這個地方。")
            path = self.grid.path(a.pos, target)
            if not path and self.grid.area_at(a.pos) != target:
                return reject(f"從這裡走不到{target}。")
            a.action = {
                "kind": "move_to",
                "target_area": target,
                "path": [p.as_list() for p in path],
                "done": not path,
            }
            self.log.write("intent", {"agent": a.id, "kind": "move_to", "target": target})
            return None

        if kind == "speak":
            utterance = (act.get("utterance") or "").strip()
            if not utterance:
                return reject("我張了口，卻沒有想說的話。")
            target_name = (act.get("target_agent") or "").strip()
            target_id = None
            if target_name:
                for oid, o in w.agents.items():
                    if o.name == target_name or oid == target_name:
                        target_id = oid
                        break
                if target_id is None:
                    return reject(f"我想跟「{target_name}」說話，但這裡沒有這個人。")
                dist = w.agents[target_id].pos.chebyshev(a.pos)
                if dist > self.cfg.hearing_radius:
                    # 距離和可見性一起記進日誌：「看得見但喊不到」和「對著根本不在
                    # 視野裡的人講話」是兩種不同的病，g6 之前分不出來。
                    return reject(
                        f"{target_name}離我太遠了（{dist} 格，超過 "
                        f"{self.cfg.hearing_radius} 格就聽不見），他聽不見。"
                        "我得先走過去，或找在旁邊的人說。",
                        dist=dist,
                        visible=dist <= self.cfg.vision_radius,
                    )
            a.action = None  # 說完就重新決定，讓對話能接下去
            ev = {
                "speaker": a.id,
                "speaker_name": a.name,
                "to": target_id,
                "utterance": utterance,
                "tick": t,
                "consumed_by": [],
            }
            self.log.write("speech", ev)
            return ev

        if kind == "attack":
            if not getattr(self.cfg, "combat", False):
                return reject("我不是那種會動手的人，這個念頭一閃就過去了。")
            target_name = (act.get("target_agent") or "").strip()
            target = None
            for oid, o in w.agents.items():
                if o.name == target_name or oid == target_name:
                    target = o
                    break
            if target is None:
                return reject(f"我想對「{target_name}」下手，但這裡沒有這個人。")
            if target.id == a.id:
                return reject("我舉起手，才發現要打的是自己。這念頭沒有道理。")
            if not target.alive:
                return reject(f"{target.name}已經倒在那裡了，再補一刀沒有意義。")
            dist = target.pos.chebyshev(a.pos)
            if dist > self.cfg.reach:
                return reject(
                    f"{target.name}離我還有 {dist} 步，這個距離出手打不到，"
                    "我得先欺身上去。",
                    dist=dist,
                )
            return self._resolve_attack(a, target)

        if kind == "use_art":
            return self._apply_art(a, act, reject)

        if kind == "interact":
            obj = (act.get("object") or "").strip() or "發呆"
            a.action = {"kind": "interact", "object": obj, "ticks_left": 2, "done": False}
            self.log.write("intent", {"agent": a.id, "kind": "interact", "object": obj})
            return None

        a.action = {"kind": "wait", "ticks_left": 1, "done": False}
        return None

    # ------------------------------------------------------------ 動作推進
    # ------------------------------------------------------------ 動手
    def _resolve_attack(self, a, target):
        """勝負由世界判定，不是由出手的人宣告。

        隨機源綁死在 (seed, tick, 誰打誰) 上——replay 必須重現同一個結果，
        否則一次血案之後整條時間線就對不上了。

        四個機制一起對抗「強者通吃」（j1b／j5 費彬連殺清場）：

          先手  只給一點便宜（+1）。原本 +2 配上技高一籌就足以讓武功相近者穩贏。
          背水  帶傷的人再出手是拿命去搏：傷勢不拖累「攻擊」，重傷更添三分狠勁，
                但「守勢」照樣因傷大減。重傷者因此是「一擊要命、門戶大開」的玻璃刀——
                被逼到角落的高手，那最後一刀最危險。這給了受害者反殺的一線生機。
          義憤  親眼見過殺人的人 fury 會漲，出手更狠（見 witness 迴圈）。
                費彬每殺一個，在場的人就更難對付——連續擊殺不再是零阻力。
          代價  取人性命的人自己也帶傷，剛猛攻勢絕技當場散掉。連殺兩人就成重傷
                玻璃刀——j5 那種「殺完還全身而退、靠一門絕技連清」被直接掐掉。
        """
        w, t, when = self.world, self.world.tick, clock_str(self.world.tick)
        rng = random.Random(f"{w.seed}:{t}:{a.id}:{target.id}")

        # 攻方：本事＋義憤＋先手＋背水－傷勢＋運氣；守方：本事＋義憤－傷勢×2＋運氣。
        # 攻擊只扣一份傷勢、又被背水抵掉（重傷還倒賺），守勢扣兩份——傷者是玻璃刀。
        desperate = 3 if a.wound >= 2 else 0
        # 絕技給的加成。運了功卻不出手就會過期——這是刻意的：
        # 工具要配合時機才有價值，光是「持有」不會讓誰變強。
        atk_art = a.buff("atk", t)
        def_art = target.buff("def", t)
        atk = a.skill + a.fury + 1 + desperate - a.wound + atk_art + rng.randint(0, 5)
        dfn = target.skill + target.fury - 2 * target.wound + def_art + rng.randint(0, 5)
        margin = atk - dfn

        hurt_target = 3 if margin >= 6 else 2 if margin >= 3 else 1 if margin >= 1 else 0
        hurt_self = 1 if margin <= -3 else 0

        if hurt_target:
            # 從全身而退到當場斃命，中間至少要挨兩次。一擊斃命會讓所有實力懸殊的
            # 遭遇在一個 tick 內結束——那既不像武俠，也讓「重傷之後怎麼辦」
            # 這段最有戲的部分永遠不會發生。要取人性命，得先把人打傷。
            target.wound = min(2 if target.wound == 0 else 3, target.wound + hurt_target)
        if hurt_self:
            a.wound = min(3, a.wound + hurt_self)

        died = [x for x in (target, a) if not x.alive and not x.killed_by]
        if not target.alive and not target.killed_by:
            target.killed_by = a.id
        if not a.alive and not a.killed_by:
            a.killed_by = target.id  # 反被格殺

        # 殺人代價：取命不是零成本。下手的人自己帶傷（封頂重傷，不自殺），
        # 攻勢絕技消散——不能靠一門大嵩陽手連清全場。
        kill_toll = False
        if not target.alive and a.alive:
            before = a.wound
            a.wound = min(2, a.wound + 1)
            kill_toll = a.wound > before
            a.buffs.pop("atk", None)

        if not target.alive:
            line = f"{a.name}向{target.name}下手，{target.name}倒了下去，沒再起來。"
            if kill_toll:
                line += f"{a.name}這一刀取了性命，自己也氣息紊亂、帶了傷。"
        elif hurt_target >= 2:
            line = f"{a.name}向{target.name}下手，{target.name}受了重傷。"
        elif hurt_target:
            line = f"{a.name}向{target.name}下手，{target.name}掛了彩。"
        elif hurt_self:
            line = f"{a.name}向{target.name}下手，反被{target.name}所傷。"
        else:
            line = f"{a.name}向{target.name}下手，被{target.name}擋了下來。"

        self.log.write("attack", {
            "attacker": a.id, "target": target.id, "margin": margin,
            "target_wound": target.wound, "attacker_wound": a.wound,
            "atk_art": atk_art, "def_art": def_art,
            "kill_toll": kill_toll,
            "line": line,
        })
        for x in died:
            self._signals.deaths.append(x.id)
            self.log.write("death", {
                "agent": x.id, "name": x.name, "killed_by": x.killed_by, "when": when,
            })

        # 動手是當場的事，不能等到下一個 tick 才讓人知道。
        # 看得見的人立刻記住——這是江湖裡消息傳開的起點。
        for other in w.agents.values():
            seen = other.pos.chebyshev(a.pos) <= self.cfg.vision_radius
            # 太遠、又不是當事人，這一拍還不知道。當事人（攻、守）一定記得。
            if other is not a and other is not target and not seen:
                continue
            if not other.alive and other not in died:
                continue  # 早就躺下的屍體不再記事（剛死的還留最後一筆）
            other.memory.add(t, when, "observation", line, importance=9)
            if other.alive and other is not a:
                # 眼前見血，先前在做的事都得放下重新盤算——不然會眼睜睜錯過一場命案，
                # 或挨了打卻繼續埋頭走路。動手的人自己的 action 在最後另外處理。
                other.action = None
                if died:
                    # 義憤：親眼見人被殺，出手更狠。+3 讓一場命案就把旁觀者推到接近封頂。
                    other.fury = min(4, other.fury + 3)

        # 有人死了，噩耗傳到他的師門親友那裡——江湖上沒有白死的人。
        for x in died:
            self._notify_kin(x, a, t)

        a.action = None if a.alive else {"kind": "wait", "ticks_left": 1, "done": False}
        return None

    # ------------------------------------------------------------ 絕技
    def _apply_art(self, a, act: dict, reject):
        """驗證一次絕技的使用。所有「用不出來」的理由都在這裡擋掉。

        絕技就是這個世界的 tool，所以這一段其實是 tool call 的參數驗證：
        招式在不在、你身上有沒有、還剩幾次、冷卻完了沒、對象夠不夠近、
        前提成不成立。任何一條不過就駁回，理由會寫回它眼前——
        和 `move_to` 走不到、`speak` 喊不到是同一套處理。
        """
        w, t = self.world, self.world.tick
        if not a.arts:
            return reject("我沒有什麼拿得出手的絕技。")

        raw = (act.get("art") or "").strip()
        art_id = arts_mod.resolve_name(raw)
        if art_id is None:
            mine = "、".join(arts_mod.CATALOG[x.id].name for x in a.arts if x.id in arts_mod.CATALOG)
            return reject(f"我沒有「{raw}」這門功夫。我會的是：{mine}。")

        slot = a.art(art_id)
        d = arts_mod.get(art_id)
        if slot is None or d is None:
            mine = "、".join(arts_mod.CATALOG[x.id].name for x in a.arts if x.id in arts_mod.CATALOG)
            return reject(f"我不會「{raw}」。我會的是：{mine}。")

        if d.combat_only and not getattr(self.cfg, "combat", False):
            return reject("這種事在這裡不會發生。")

        ok, why = slot.available(t)
        if not ok:
            if why == "used_up":
                return reject(f"{d.name}今天已經用盡了，再使不出來。")
            return reject(f"{d.name}剛使過，這會兒運不上勁，還得緩一緩。")

        if d.params.get("require_wound") and a.wound <= 0:
            return reject(f"{d.name}是帶著傷才使得上的，我這會兒好端端的，用不著。")

        # --- 對象 ---
        target = None
        if d.target == arts_mod.TARGET_AGENT:
            name = (act.get("target_agent") or "").strip()
            if not name:
                return reject(
                    f"使{d.name}得說出對象是誰——`target_agent` 填對方的名字，不能空著。"
                )
            for oid, o in w.agents.items():
                if o.name == name or oid == name:
                    target = o
                    break
            if target is None:
                return reject(f"我想對「{name}」使{d.name}，但這裡沒有這個人。")
            if target.id == a.id:
                return reject(f"{d.name}沒有對自己使的道理。")
            if not target.alive:
                return reject(f"{target.name}已經沒氣了，這時候使{d.name}沒有意義。")
            if d.reach:
                dist = target.pos.chebyshev(a.pos)
                if dist > d.reach:
                    return reject(
                        f"{target.name}離我還有 {dist} 步，這個距離使不出{d.name}，"
                        f"得再近一些（{d.reach} 格以內）。",
                        dist=dist,
                    )
        return self._resolve_art(a, slot, d, target, act, reject)

    def _resolve_art(self, a, slot, d, target, act: dict, reject):
        """效果真的發生。到這裡為止所有前提都驗過了。

        除了 `rite`（辦不成就整個不算）以外，走到這裡就一定會消耗配額——
        用了沒達到預期效果也是結果的一部分，那正是我們想讓角色學到的事。
        """
        w, t, when = self.world, self.world.tick, clock_str(self.world.tick)
        p = d.params
        detail: dict = {}
        speech_ev = None
        line = f"{a.name}使出了{d.name}。"

        def spend():
            if slot.uses_left > 0:
                slot.uses_left -= 1
            slot.used += 1
            slot.ready_at = t + d.cooldown if d.cooldown else -1

        def remember(who, text, importance=8):
            who.memory.add(t, when, "observation", text, importance=importance)

        def bystanders(radius):
            return [
                o for o in w.agents.values()
                if o.alive and o is not a and o.pos.chebyshev(a.pos) <= radius
            ]

        if d.effect in ("atk_up", "def_up", "dash", "veil"):
            name = {"atk_up": "atk", "def_up": "def", "dash": "dash", "veil": "veil"}[d.effect]
            amount = int(p.get("amount", p.get("multiplier", 1)))
            ticks = int(p.get("ticks", 3))
            a.buffs[name] = {"amount": amount, "until": t + ticks - 1}
            detail = {"buff": name, "amount": amount, "until": t + ticks - 1}
            line = {
                "atk_up": f"{a.name}運起{d.name}，氣勢陡然一變。",
                "def_up": f"{a.name}擺開{d.name}的架式，守得滴水不漏。",
                "dash": f"{a.name}提氣使出{d.name}，腳下快了不止一倍。",
                "veil": f"{a.name}不著痕跡地收拾了一下行止，一時看不出來歷。",
            }[d.effect]
            remember(a, f"我使了{d.name}。", importance=5)

        elif d.effect == "soothe":
            amount, radius = int(p.get("amount", 2)), int(p.get("radius", 3))
            cooled = []
            for o in [a, *bystanders(radius)]:
                if o.fury > 0:
                    o.fury = max(0, o.fury - amount)
                    cooled.append(o.id)
                if o is not a:
                    remember(o, f"{a.name}的{d.name}傳進耳裡，心裡那股躁動平了些。", 6)
            detail = {"cooled": cooled, "radius": radius}
            line = f"{a.name}使出{d.name}，四下的火氣淡了下來。"
            remember(a, f"我使了{d.name}。", importance=5)

        elif d.effect == "denounce":
            claim = p.get("claim", "")
            # veil 擋得掉一次：擋掉之後就失效，不會一直擋。
            landed = target.buff("veil", t) == 0
            if not landed:
                target.buffs.pop("veil", None)
            witnesses = [o.id for o in bystanders(self.cfg.hearing_radius)]
            utterance = (act.get("utterance") or "").strip() or (
                f"「各位看清楚了，{target.name}{claim}，這事我有實據。」"
            )
            self._signals.exposures.append({
                "by": a.id, "target": target.id, "claim": claim,
                "landed": landed, "witnesses": witnesses,
            })
            if landed:
                line = f"{a.name}當眾指證{target.name}{claim}。"
                for o in [target, *bystanders(self.cfg.hearing_radius)]:
                    remember(o, f"{a.name}當眾指證{target.name}{claim}。", 10)
            else:
                line = f"{a.name}指著{target.name}說了幾句，卻沒人聽出個所以然來。"
                remember(a, f"我指證{target.name}，話卻沒有落到實處。", 8)
            detail = {"target": target.id, "claim": claim,
                      "landed": landed, "witnesses": witnesses}
            # 指證是當眾說出來的話——讓它走 speech 這條路，被指的人才有機會當場回嘴。
            speech_ev = {
                "speaker": a.id, "speaker_name": a.name, "to": target.id,
                "utterance": utterance, "tick": t, "consumed_by": [],
            }

        elif d.effect == "lure":
            utterance = (act.get("utterance") or "").strip()
            if not utterance:
                return reject(f"我要使{d.name}，卻沒想好要說什麼。")
            remember(
                target,
                f"{a.name}對我說：「{utterance}」這話聽起來竟然頗有道理。",
                importance=int(p.get("weight", 8)),
            )
            detail = {"target": target.id}
            line = f"{a.name}對{target.name}說了一番話，聽的人神色鬆動。"
            speech_ev = {
                "speaker": a.id, "speaker_name": a.name, "to": target.id,
                "utterance": utterance, "tick": t, "consumed_by": [],
            }

        elif d.effect == "scout":
            area = self.grid.area_at(target.pos) or "路上"
            text = f"（打聽到了：{target.name}這會兒在{area}。）"
            remember(a, text, importance=8)
            # 記憶不保證被檢索到，所以也直接送到他下一拍的眼前——和報噩耗同一條路。
            if self.director is not None:
                self.director.add_runtime(a.id, text, t + 1, tag="scout")
            detail = {"target": target.id, "area": area}
            line = f"{a.name}向路邊的人打聽了幾句。"

        elif d.effect == "rite":
            area = p.get("area", "")
            here = self.grid.area_at(a.pos)
            if area and here != area:
                return reject(f"{d.name}得在{area}才辦得成，我人還在{here or '路上'}。")
            need = int(p.get("witnesses", 0))
            watching = bystanders(self.cfg.vision_radius)
            if len(watching) < need:
                return reject(
                    f"{d.name}是做給人看的大禮，這會兒身邊沒有人，辦了也不算數。"
                    "我得等人到齊。"
                )
            rite = p.get("rite", d.name)
            self._signals.rites.setdefault(a.id, []).append(rite)
            line = f"{a.name}當眾行了{rite}之禮。"
            for o in watching:
                remember(o, line, 10)
            remember(a, f"我把{rite}做完了。", 10)
            detail = {"rite": rite, "area": here, "witnesses": [o.id for o in watching]}

        else:  # 目錄裡有、引擎沒實作——設定錯誤，不要靜靜吞掉
            return reject(f"{d.name}我一時竟使不出來。")

        spend()
        self.log.write("art_used", {
            "agent": a.id, "name": a.name, "art": d.id, "art_name": d.name,
            "kind": d.kind, "effect": d.effect, "line": line,
            "uses_left": slot.uses_left, **detail,
        })
        if self.console:
            self.console.print(f"[magenta]✦ {line}[/magenta]")

        # 使完就重新盤算下一步（運功之後總要真的出手）。
        a.action = None
        if speech_ev:
            self.log.write("speech", speech_ev)
        return speech_ev

    def _on_goal_closed(self, rec: dict) -> None:
        """目的結案了，讓當事人自己知道——否則他會繼續朝一個已經結束的事使力。"""
        a = self.world.agents.get(rec["agent"])
        if a is None or not a.alive:
            return
        t, when = self.world.tick, clock_str(self.world.tick)
        if rec["status"] == "done":
            text = f"（{rec['text']}——{rec['note']}。這件事了了。）"
        else:
            text = f"（{rec['text']}——{rec['note']}。這條路走不通了。）"
        a.memory.add(t, when, "observation", text, importance=10)
        if self.director is not None:
            self.director.add_runtime(a.id, text, t + 1, tag="goal")
        if self.console:
            colour = "green" if rec["status"] == "done" else "red"
            self.console.print(f"[{colour}]◆ {a.name}：{rec['text']}／{rec['note']}[/{colour}]")

    def _notify_kin(self, dead, killer, t: int) -> None:
        """把噩耗＋尋仇的念頭塞進死者親友。

        兩條路並行：
          1. 高 importance 記憶——跟著 WorldState／checkpoint，fork 不會丟。
          2. 導演 runtime inject（下一拍）——obs.injected 觸發 needs_llm，
             逼親友當場面對「要不要討公道」。

        親眼看見的人不重複報信：他們的義憤和記憶已經夠了。
        """
        when = clock_str(t)
        for other in self.world.agents.values():
            if dead.id not in other.kin or not other.alive or other is killer:
                continue
            if other.pos.chebyshev(dead.pos) <= self.cfg.vision_radius:
                continue  # 親眼看見的，不必再報一次
            text = (
                f"（有人急奔來報：{dead.name}死了，是{killer.name}下的手。"
                f"你和他的交情，你自己心裡有數。江湖上沒有白死的人。）"
            )
            # 記憶一定寫下：runtime inject 不進 checkpoint，fork 會漏待發尋仇；
            # 高 importance 記憶跟著 WorldState 走，接力／重放都還在。
            other.memory.add(t, when, "observation", text, importance=10)
            if self.director is not None:
                self.director.add_runtime(other.id, text, t + 1, tag="revenge")
            self.log.write(
                "revenge_seed",
                {"mourner": other.id, "dead": dead.id, "killer": killer.id, "when": when},
            )

    def _advance(self, a) -> None:
        act = a.action
        if not act or act.get("done"):
            return
        t, when = self.world.tick, clock_str(self.world.tick)

        if act["kind"] == "move_to":
            path = act.get("path") or []
            # 輕功類的絕技在這裡兌現：腳程加倍，一拍走得比別人遠。
            speed = self.cfg.move_speed * max(1, a.buff("dash", t))
            steps = path[:speed]
            if steps:
                a.pos = Pos.of(steps[-1])
            act["path"] = path[speed:]
            if not act["path"]:
                act["done"] = True
                a.memory.add(
                    t, when, "observation", f"我到了{act['target_area']}。",
                    cognition.IMPORTANCE["arrival"],
                )
                self.log.write("arrive", {"agent": a.id, "area": act["target_area"]})
        else:
            act["ticks_left"] = act.get("ticks_left", 1) - 1
            if act["ticks_left"] <= 0:
                act["done"] = True

    # ------------------------------------------------------------ reflection
    async def _reflect_phase(self) -> None:
        w, t = self.world, self.world.tick
        due = [a for a in w.agents.values() if cognition.should_reflect(a, self.cfg)]
        if not due:
            return
        calls = [
            cognition.reflection_call(a, self.world_block_text, self.cfg, t) for a in due
        ]
        results = await self.llm.run_batch(calls)
        for a in due:
            res = results.get(f"{t}:{a.id}:reflect")
            if isinstance(res, Exception) or res is None:
                self.failed_calls += 1
                self.last_error = str(res)
                self.log.write("reflect_failed", {"agent": a.id, "error": str(res)})
                a.memory.importance_since_reflection = 0  # 別卡在無限重試
                continue
            insights = cognition.apply_reflection(a, res, self.cfg, t)
            self.log.write(
                "reflection",
                {"agent": a.id, "insights": insights, "beliefs": a.memory.beliefs},
            )
            if self.console:
                self.console.print(f"[dim]※ {a.name} 想通了：{'；'.join(insights[:2])}[/dim]")

    # ------------------------------------------------------------ 覺察評分
    async def _awareness_phase(self, force: bool = False) -> None:
        w, t = self.world, self.world.tick
        if force:
            # 剛評過就別再評一次（跑的 tick 數正好是 judge_interval 倍數時會撞上）
            if t - self._last_judge_tick <= 1:
                return
        elif t == 0 or t % self.cfg.judge_interval != 0:
            return
        # 箱庭劇本沒有主角，覺察評審整層不存在——連呼叫都不該發生。
        p = w.protagonist_or_none()
        if p is None:
            return
        call = awareness.judge_call(w, p, self.cfg)
        if call is None:
            return
        results = await self.llm.run_batch([call])
        res = results.get(call.key)
        if isinstance(res, Exception) or res is None:
            self.log.write("judge_failed", {"error": str(res)})
            return
        awareness.apply_judgement(w, res, self.log)
        self._last_judge_tick = t
        if self.console:
            self.console.print(
                f"[bold yellow]覺察評分 {res.get('score')}/10[/bold yellow] "
                f"— {res.get('rationale','')}"
            )

    # ------------------------------------------------------------ 顯示
    def _echo(self, a, res: dict) -> None:
        act = res.get("action") or {}
        colour = "bold cyan" if a.is_protagonist else "white"
        line = f"[{colour}]{a.name}[/{colour}] [dim]{res.get('thought','')}[/dim]"
        if act.get("kind") == "speak":
            line += f"\n    → 「{act.get('utterance','')}」"
        elif act.get("kind") == "move_to":
            line += f"\n    → 前往 {act.get('target_area','')}"
        elif act.get("kind") == "interact":
            line += f"\n    → {act.get('object','')}"
        self.console.print(line)
