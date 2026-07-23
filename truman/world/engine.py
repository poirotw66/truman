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

from dataclasses import dataclass, field
from pathlib import Path

from ..agents import cognition
from ..config import clock_str
from ..director import awareness
from ..obs import checkpoint
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
    _last_judge_tick: int = -999  # 收工強制評審用來避免重複評

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

    async def tick(self) -> None:
        w, t = self.world, self.world.tick
        self.log.write("tick_start", {"tick": t, "when": clock_str(t)})

        injections = self.director.apply(w, self.grid)
        obs = build_observations(w, self.grid, self.pending_speech, injections, self.cfg)

        for aid, a in w.agents.items():
            cognition.record_perception(a, obs[aid], self.cfg, t)

        # --- 主決策階段 ---
        thinkers: list[tuple[str, str]] = []
        for aid, a in w.agents.items():
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
        for aid, a in w.agents.items():
            self._advance(a)

        await self._reflect_phase()
        await self._awareness_phase()

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
                continue

            self.ok_calls += 1
            a.last_think_tick = t
            a.think_count += 1
            a.plan = (res.get("plan") or a.plan).strip()
            cognition.record_decision(a, res, t)

            ev = self._apply_intent(a, res.get("action") or {})
            if ev:
                speech.append(ev)

            if a.is_protagonist:
                awareness.score_tick(
                    w,
                    a,
                    res.get("thought", ""),
                    (res.get("action") or {}).get("utterance", ""),
                    self.cfg,
                    self.log,
                )
            if self.console:
                self._echo(a, res)
        return speech

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

        if kind == "interact":
            obj = (act.get("object") or "").strip() or "發呆"
            a.action = {"kind": "interact", "object": obj, "ticks_left": 2, "done": False}
            self.log.write("intent", {"agent": a.id, "kind": "interact", "object": obj})
            return None

        a.action = {"kind": "wait", "ticks_left": 1, "done": False}
        return None

    # ------------------------------------------------------------ 動作推進
    def _advance(self, a) -> None:
        act = a.action
        if not act or act.get("done"):
            return
        t, when = self.world.tick, clock_str(self.world.tick)

        if act["kind"] == "move_to":
            path = act.get("path") or []
            steps = path[: self.cfg.move_speed]
            if steps:
                a.pos = Pos.of(steps[-1])
            act["path"] = path[self.cfg.move_speed :]
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
