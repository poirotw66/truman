"""離線煙霧測試：不呼叫 API，驗證 tick 迴圈、intent 驗證、序列化、分支。

    python -m tests.smoke
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenarios import seahaven  # noqa: E402
from truman.config import PROVIDERS, SimConfig, clock_str  # noqa: E402
from truman.director.director import Director  # noqa: E402
from truman.llm.client import make_client  # noqa: E402
from truman.llm.prompts import persona_block, world_block  # noqa: E402
from truman.llm.schemas import ACTION_SCHEMA  # noqa: E402
from truman.llm.tokens import estimate  # noqa: E402
from truman.obs import checkpoint  # noqa: E402
from truman.obs.eventlog import EventLog  # noqa: E402
from truman.world.engine import Engine  # noqa: E402
from truman.world.observation import build_observations  # noqa: E402
from truman.world.grid import Pos  # noqa: E402
from truman.world.state import AgentState, WorldState  # noqa: E402

AREAS = ["咖啡館", "廣場", "報攤", "圖書館", "公園", "海堤", "保險行"]


@dataclass
class StubLLM:
    """依 key 產生決定性的假回應，涵蓋所有 action kind 與一次非法 intent。"""

    cfg: object
    log: object
    n: int = 0
    seen_keys: list[str] = field(default_factory=list)

    def stats(self):
        return {"_total_cost_usd": 0.0}

    async def run_batch(self, calls):
        out = {}
        for c in calls:
            self.seen_keys.append(c.key)
            self.n += 1
            out[c.key] = self._fake(c)
        return out

    def _fake(self, c):
        if c.key.endswith(":reflect"):
            return {
                "insights": [f"我發現{c.key.split(':')[1]}最近很反常。"],
                "beliefs": ["這個鎮上的日子太規律了。"],
            }
        if c.key.endswith(":awareness"):
            return {"score": 3, "evidence": ["太巧了"], "rationale": "stub"}

        i = self.n
        if i % 7 == 3:
            return {
                "thought": "這也太巧了吧，總覺得不對勁。",
                "action": {"kind": "speak", "target_agent": "", "utterance": "今天天氣真好。",
                           "target_area": "", "object": ""},
                "plan": "找人聊聊。",
            }
        if i % 7 == 5:  # 故意送一個不存在的地點，測 intent 駁回
            return {
                "thought": "去那邊看看。",
                "action": {"kind": "move_to", "target_area": "火星基地",
                           "target_agent": "", "utterance": "", "object": ""},
                "plan": "亂走。",
            }
        if i % 7 == 6:
            return {
                "thought": "先喝杯東西。",
                "action": {"kind": "interact", "object": "翻報紙",
                           "target_area": "", "target_agent": "", "utterance": ""},
                "plan": "待著。",
            }
        return {
            "thought": "該走了。",
            "action": {"kind": "move_to", "target_area": AREAS[i % len(AREAS)],
                       "target_agent": "", "utterance": "", "object": ""},
            "plan": f"去{AREAS[i % len(AREAS)]}。",
        }


def build(run_dir: Path, world=None):
    grid = seahaven.build_grid()
    cfg = SimConfig(judge_interval=6, reflection_threshold=25, checkpoint_interval=5)
    world = world or seahaven.build_world("smoke", 7)
    log = EventLog(run_dir)
    log.bind_tick(lambda: world.tick)
    llm = StubLLM(cfg=cfg, log=log)
    engine = Engine(
        world=world, grid=grid, cfg=cfg, llm=llm,
        director=Director(script=list(seahaven.DIRECTOR_SCRIPT), log=log),
        log=log, world_block_text=world_block(grid, seahaven.BRIEF, seahaven.NORMS, seahaven.PUBLIC_CAST),
        run_dir=run_dir, console=None,
    )
    return engine, log, llm, grid


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    return ok


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


class _U:  # 假的 usage 物件
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _gem_usage(*, total_in, cached, total_out, thoughts, grand) -> dict:
    from truman.llm.providers.gemini_client import _usage

    return _usage(_U(usage=_U(
        total_input_tokens=total_in, total_cached_tokens=cached,
        total_output_tokens=total_out, total_thought_tokens=thoughts,
        total_tokens=grand,
    )))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="truman_smoke_"))
    failures = 0
    try:
        # ---- 地圖 ----
        grid = seahaven.build_grid()
        print("地圖")
        failures += not check("尺寸 24x16", (grid.w, grid.h) == (24, 16), f"{grid.w}x{grid.h}")
        failures += not check("區域全部落在可通行格上", all(
            any(grid.walkable(Pos(x, y))
                for y in range(a.y0, a.y1 + 1) for x in range(a.x0, a.x1 + 1))
            for a in grid.areas.values()))
        p = grid.path(Pos(2, 2), "報攤")
        failures += not check("陳家 → 報攤 有路", len(p) > 0, f"{len(p)} 步")
        failures += not check("區域名容錯解析", grid.resolve_area("咖啡") == "咖啡館")
        failures += not check("不存在的地點回 None", grid.resolve_area("火星基地") is None)

        # ---- prompt 區塊穩定性（快取的前提）----
        print("\nprompt 快取前綴")
        wb1 = world_block(grid, seahaven.BRIEF, seahaven.NORMS, seahaven.PUBLIC_CAST)
        wb2 = world_block(
            seahaven.build_grid(), seahaven.BRIEF, seahaven.NORMS, seahaven.PUBLIC_CAST
        )
        failures += not check("世界區塊 byte 級穩定", wb1 == wb2)
        # 靜默快取殺手：任何逐請求變動的值出現在前綴，快取就永遠 0 命中。
        # 靜態作息表裡的 "06:00" 是合法的；會變的是模擬時鐘 "第N天 HH:MM"。
        volatile = [clock_str(t) for t in (0, 1, 5, 96, 97)] + ["tick "]
        failures += not check("世界區塊不含逐請求變動值",
                              not any(s in wb1 for s in volatile))

        # 不對稱是這個劇本的全部重點：共用區塊絕不能洩漏誰是演員
        leak = ("演員", "攝影", "節目", "製作組", "劇組", "劇本")
        failures += not check("世界區塊未洩漏演員身分", not any(s in wb1 for s in leak))
        w0 = seahaven.build_world("t", 0)
        failures += not check("主角人設未洩漏",
                              not any(s in persona_block(w0.protagonist()) for s in leak))
        failures += not check("演員人設有拿到守則",
                              "攝影棚" in persona_block(w0.agents["wang_hao"]))

        # 實際判定要看「世界＋人設」的累積前綴，不是世界區塊單獨的大小。
        bp1 = estimate(wb1)
        bp2 = min(estimate(wb1 + persona_block(a)) for a in w0.agents.values())
        print(f"        世界 ~{bp1} tokens、世界＋人設 ~{bp2} tokens（保守下界）")
        for prov in sorted(PROVIDERS):
            c = SimConfig(provider=prov)
            floors = sorted({c.cache_min(m) for m in c.models.values()})
            print(f"          {prov:<10} 門檻 {floors}")
        print("        逐層判定與真值量測請跑：python -m truman.cli tokens --provider ...")

        # ---- tick 迴圈 ----
        print("\ntick 迴圈")
        engine, log, llm, _ = build(tmp / "a")
        asyncio.run(engine.run(12))
        failures += not check("跑完 12 tick", engine.world.tick == 12)
        failures += not check("有 LLM 呼叫", llm.n > 0, f"{llm.n} 次")
        failures += not check("所有 agent 都站在可通行格",
                              all(grid.walkable(a.pos) for a in engine.world.agents.values()))
        failures += not check("有記憶寫入", all(
            len(a.memory.entries) > 0 for a in engine.world.agents.values()))
        log.close()

        events = list(EventLog.read(tmp / "a"))
        types = {e["type"] for e in events}
        for want in ("tick_start", "think", "intent", "speech", "invalid_intent",
                     "awareness", "reflection", "checkpoint", "director"):
            failures += not check(f"事件日誌含 {want}", want in types)
        failures += not check("節流有生效", any(e["type"] == "coast" for e in events))

        # 迴歸測試：judge 掛在 tick % interval 上，跑 N tick 走的是 tick 0..N-1，
        # 所以最後一段軌跡永遠評不到——收工必須強制補評一次（g5 跑 48 tick 只評到 1 次）。
        judged = [e for e in events if e["data"].get("source") == "llm_judge"]
        failures += not check("收工有強制評審一次",
                              bool(judged) and judged[-1]["tick"] == engine.world.tick,
                              f"評了 {len(judged)} 次，最後一次在 tick "
                              f"{judged[-1]['tick'] if judged else '—'}")
        before_n = llm.n
        asyncio.run(engine._awareness_phase(force=True))
        failures += not check("剛評過不會重複評", llm.n == before_n)

        # 迴歸測試：CLI 有自己的 tick 迴圈，不走 Engine.run()。收工評審只掛在 run()
        # 上的話，真實路徑永遠不會執行——g6 就是這樣白跑了 48 tick 才發現。
        from truman import cli as cli_mod  # noqa: PLC0415

        eng2, log2, llm2, _ = build(tmp / "c")
        asyncio.run(cli_mod._drive(eng2, log2, llm2, 8, quiet=True))
        judged2 = [e for e in EventLog.read(tmp / "c")
                   if e["data"].get("source") == "llm_judge"]
        failures += not check("CLI 路徑收工也會評審",
                              bool(judged2) and judged2[-1]["tick"] == 8,
                              f"最後一次在 tick {judged2[-1]['tick'] if judged2 else '—'}")

        # 迴歸測試：哨兵原本是無上限累加器，g5 跑到 10.5，和評審的 0–10 不同尺度。
        print("\n覺察哨兵封頂")
        from truman.director import awareness as aw  # noqa: PLC0415

        p0 = engine.world.protagonist()
        loud = "太巧了，這一切都是假的，一模一樣的劇本又重複了一次"
        sink = type("Sink", (), {"write": lambda self, *a: None})()  # log 上面關掉了
        for _ in range(40):
            aw.score_tick(engine.world, p0, loud, "", engine.cfg, sink)
        failures += not check("哨兵封頂在 10", engine.world.awareness_score == 10.0,
                              str(engine.world.awareness_score))
        failures += not check("撞頂後仍記錄命中（證據鏈不能斷）",
                              engine.world.awareness_log[-1]["source"] == "pattern")

        # ---- 序列化往返 ----
        print("\n序列化 / checkpoint")
        d = engine.world.to_dict()
        json.dumps(d, ensure_ascii=False)  # 必須可 JSON 化
        rt = WorldState.from_dict(json.loads(json.dumps(d, ensure_ascii=False)))
        failures += not check("WorldState 往返一致", rt.to_dict() == d)
        cp = checkpoint.latest(tmp / "a")
        failures += not check("checkpoint 存在", cp is not None, str(cp and cp.name))

        # ---- 分支 ----
        print("\n分支")
        forked = checkpoint.fork(cp, "smoke_fork")
        base_tick = forked.tick
        e2, log2, _, _ = build(tmp / "b", world=forked)
        e2.director.add_runtime("chen_yuan", "（你找到一張沒見過的船票。）", base_tick)
        asyncio.run(e2.run(3))
        log2.close()
        failures += not check("分支續跑", e2.world.tick == base_tick + 3)
        failures += not check("注入有進入主角記憶", any(
            "船票" in m.content for m in e2.world.protagonist().memory.entries))

        # ---- provider 抽象層 ----
        print("\nprovider")
        for prov in sorted(PROVIDERS):
            c = SimConfig(provider=prov)
            missing_price = [m for m in c.models.values() if c.price(m) == (0.0, 0.0, 0.0)]
            failures += not check(f"{prov} 每層都有價格", not missing_price,
                                  str(missing_price))
            failures += not check(
                f"{prov} 可建立 client（replay 模式，免憑證）",
                make_client(cfg=c, log=EventLog(tmp / f"p_{prov}"), replay={}).provider == prov)
        failures += not check("未知 provider 會被擋下",
                              _raises(lambda: SimConfig(provider="nope")))

        # 迴歸測試：Interactions 的 response_format 是「格式物件本身」，
        # 不是 {"text": {...}}。傳成後者不會報錯，只會安靜地不生效——
        # 模型改吐自由文字，token 燒完才在 JSON 解析那步發現。
        # 注意要驗 Interactions 的 TextResponseFormat，不是 google.genai.types 的
        # 同名類別（那是 generate_content 的，結構不同，驗了會給假陽性）。
        try:
            from google.genai._gaos.types.interactions.textresponseformat import (
                TextResponseFormat,
            )

            from truman.llm.providers.gemini_client import text_json_format

            rf = text_json_format(ACTION_SCHEMA)
            failures += not check("Gemini response_format 沒有多包一層",
                                  rf.get("type") == "text" and "text" not in rf)
            failures += not check("Gemini 用 TypedDict 欄位名",
                                  {"mime_type", "schema_"} <= set(rf))
            m = TextResponseFormat.model_validate(
                {"type": "text", "mime_type": rf["mime_type"], "schema": rf["schema_"]}
            )
            failures += not check("通過 Interactions 型別驗證",
                                  m.mime_type == "application/json" and m.schema_ is not None)
        except ImportError as e:
            print(f"  SKIP  google-genai 未安裝或內部路徑變動，跳過形狀驗證（{e}）")

        # Gemini 用量換算：total_input_tokens 含快取，要扣掉才不會重複計價；
        # thought tokens 是否已含在 output 裡，用 total_tokens 反推。
        print("\nGemini 用量換算")
        u = _gem_usage(total_in=3000, cached=2200, total_out=250, thoughts=80, grand=3250)
        failures += not check("扣掉快取部分", u["inp"] == 800, str(u["inp"]))
        failures += not check("快取計入 c_read", u["c_read"] == 2200)
        failures += not check("output 已含 thoughts 時不重複加", u["out"] == 250, str(u["out"]))
        u2 = _gem_usage(total_in=3000, cached=0, total_out=250, thoughts=80, grand=3330)
        failures += not check("output 未含 thoughts 時補上", u2["out"] == 330, str(u2["out"]))

        # ---- 對話追加輪的消化標記 ----
        # 迴歸測試：consumed_by 必須逐 (事件, 對象) 生效，
        # 不能把別人沒回應過的話也一起吞掉。
        print("\n對話追加輪")
        w = seahaven.build_world("dlg", 1)
        a, b, c = w.agents["chen_yuan"], w.agents["lin_shu"], w.agents["wang_hao"]
        b.pos = c.pos = a.pos  # 三個人站在一起，彼此都聽得見
        ev_to_b = {"speaker": "chen_yuan", "speaker_name": "陳原", "to": "lin_shu",
                   "utterance": "妳今天要值班嗎？", "tick": 0, "consumed_by": ["lin_shu"]}
        ev_open = {"speaker": "chen_yuan", "speaker_name": "陳原", "to": None,
                   "utterance": "外面好熱。", "tick": 0, "consumed_by": []}
        o = build_observations(w, grid, [ev_to_b, ev_open], {}, SimConfig())
        failures += not check("已回應者不再收到同一句",
                              all(h["utterance"] != ev_to_b["utterance"]
                                  for h in o["lin_shu"].heard))
        failures += not check("已回應者仍收得到其他話",
                              any(h["utterance"] == ev_open["utterance"]
                                  for h in o["lin_shu"].heard))
        failures += not check("旁人兩句都收得到", len(o["wang_hao"].heard) == 2,
                              f"{len(o['wang_hao'].heard)} 句")
        failures += not check("說話者聽不見自己", len(o["chen_yuan"].heard) == 0)

        # ---- 聽力射程要寫進 observation ----
        # 迴歸測試：vision(5) > hearing(3)，agent 算不出距離，射程名單不明講的話
        # 它就會對著看得見卻聽不見的人講話，被 _apply_intent 駁回（g4 佔 13% 的 intent）。
        print("\n聽力射程")
        w = seahaven.build_world("ear", 1)
        cfg_ear = SimConfig()
        w.agents["chen_yuan"].pos = Pos(8, 8)
        w.agents["lin_shu"].pos = Pos(10, 8)  # 距離 2：聽得見
        w.agents["wang_hao"].pos = Pos(12, 8)  # 距離 4：看得見、聽不見
        for other in ("mei_yi", "guo_bo", "su_qing"):
            w.agents[other].pos = Pos(22, 14)  # 挪遠，別干擾
        o = build_observations(w, grid, [], {}, cfg_ear)
        vis = {v["name"]: v["hearable"] for v in o["chen_yuan"].visible}
        failures += not check("看得見的人都有標 hearable",
                              set(vis) == {"林淑", "王浩"}, str(sorted(vis)))
        failures += not check("距離 2 聽得見、距離 4 聽不見",
                              vis.get("林淑") is True and vis.get("王浩") is False, str(vis))
        text = o["chen_yuan"].render()
        failures += not check("射程名單有寫進 render",
                              "聽得見你說話的只有：林淑。" in text)
        failures += not check("聽不見的人不進射程名單",
                              "聽得見你說話的只有：林淑、王浩" not in text)

        w.agents["lin_shu"].pos = Pos(12, 8)  # 兩個都挪到聽力範圍外
        o = build_observations(w, grid, [], {}, cfg_ear)
        failures += not check("全部太遠時明講沒人聽得見",
                              "沒有人聽得見" in o["chen_yuan"].render())

        # ---- 駁回回饋要進下一個 tick 的眼前 ----
        # 迴歸測試：只寫進記憶不夠，檢索不保證撈得到——g6 裡林淑連續五個 tick
        # 對著一個聽不見的人講同一件事。
        print("\n駁回回饋")
        w.agents["chen_yuan"].last_rejection = "梅姨離我太遠了，他聽不見。"
        o = build_observations(w, grid, [], {}, cfg_ear)
        failures += not check("駁回理由出現在 observation",
                              "你上一步沒有做成：梅姨離我太遠了，他聽不見。"
                              in o["chen_yuan"].render())
        failures += not check("沒被駁回的人不會多這一行",
                              "你上一步沒有做成" not in o["lin_shu"].render())

        eng3, log3, llm3, _ = build(tmp / "d")
        a3 = eng3.world.agents["chen_yuan"]
        a3.last_rejection = "測試：上一步沒做成。"
        obs3 = build_observations(eng3.world, grid, [], {}, eng3.cfg)
        failures += not check("駁回理由有進 prompt",
                              "測試：上一步沒做成。" in obs3["chen_yuan"].render())
        asyncio.run(eng3._decide([("chen_yuan", "forced")], obs3, "act"))
        failures += not check("送進 prompt 之後就清掉", a3.last_rejection == "",
                              repr(a3.last_rejection))
        log3.close()

        # ---- 武林：動手、傷、死 ----
        print("\n動手")
        from scenarios import jianghu  # noqa: PLC0415

        blood = type("Sink", (), {"write": lambda self, *a: None})()  # log 已關

        def duel(attacker: str, target: str, tick: int = 0, combat: bool = True):
            w = jianghu.build_world("duel", 7)
            w.tick = tick
            eng = Engine(
                world=w, grid=jianghu.build_grid(), cfg=SimConfig(combat=combat),
                llm=None, director=None, log=blood, world_block_text="",
                run_dir=tmp / "e",
            )
            a, b = w.agents[attacker], w.agents[target]
            b.pos = Pos(a.pos.x + 1, a.pos.y)
            eng._apply_intent(a, {"kind": "attack", "target_agent": b.name})
            return w, a, b

        _, tian, yilin = duel("tian_boguang", "yi_lin")
        failures += not check("高手一招打得動低手", yilin.wound > 0, yilin.wound_word)
        failures += not check("但不會一擊斃命（要死得先受傷）", yilin.alive, yilin.wound_word)
        w2, tian2, yilin2 = duel("tian_boguang", "yi_lin", tick=1)
        yilin2.wound = 2
        Engine(world=w2, grid=jianghu.build_grid(), cfg=SimConfig(combat=True),
               llm=None, director=None, log=blood, world_block_text="",
               run_dir=tmp / "e")._apply_intent(
                   tian2, {"kind": "attack", "target_agent": "儀琳"})
        failures += not check("重傷的人會被打死", not yilin2.alive, yilin2.wound_word)
        failures += not check("死者記得是誰下的手", yilin2.killed_by == "tian_boguang",
                              yilin2.killed_by)

        # 迴歸測試：勝負綁死在 (seed, tick, 誰打誰)，否則 replay 一遇血案就分岔。
        outs = {duel("tian_boguang", "yi_lin", tick=3)[2].wound for _ in range(3)}
        failures += not check("同 seed/tick 的勝負可重現", len(outs) == 1, str(outs))

        # 和平劇本不該有這個動作——連 schema 的 enum 都不給。
        _, _, yilin3 = duel("tian_boguang", "yi_lin", combat=False)
        failures += not check("和平劇本駁回動手", yilin3.wound == 0)
        from truman.llm.schemas import action_schema  # noqa: PLC0415

        peace_kinds = action_schema(False)["properties"]["action"]["properties"]["kind"]["enum"]
        war_kinds = action_schema(True)["properties"]["action"]["properties"]["kind"]["enum"]
        failures += not check("attack 只出現在 combat schema",
                              "attack" not in peace_kinds and "attack" in war_kinds)
        failures += not check("動手規則只進 combat 劇本的世界區塊",
                              "attack" in cli_mod.scenario_world_block(
                                  jianghu, jianghu.build_grid())
                              and "attack" not in cli_mod.scenario_world_block(
                                  seahaven, grid))

        # ---- 武林：對抗「強者通吃」的三個機制 ----
        # 背景：j1b 費彬六戰全勝、零反抗、一人清城。以下驗證新加的平衡機制。
        def war_engine(world, director=None, run="e"):
            return Engine(
                world=world, grid=jianghu.build_grid(), cfg=SimConfig(combat=True),
                llm=None, director=director, log=blood, world_block_text="",
                run_dir=tmp / run,
            )

        print("\n義憤")
        # 親眼見人被殺，旁觀者 fury 上升、放下手邊的事重新盤算；下手的人自己不義憤。
        w = jianghu.build_world("fury", 7)
        w.tick = 5
        fei, yilin, lh = w.agents["fei_bin"], w.agents["yi_lin"], w.agents["linghu_chong"]
        yilin.wound = 2
        yilin.pos = Pos(fei.pos.x + 1, fei.pos.y)      # 貼著費彬，這一刀會死
        lh.pos = Pos(fei.pos.x + 1, fei.pos.y + 1)     # 站在旁邊看得見
        lh.action = {"kind": "interact", "object": "喝酒", "ticks_left": 2, "done": False}
        fury_fei0, fury_lh0 = fei.fury, lh.fury
        war_engine(w)._apply_intent(fei, {"kind": "attack", "target_agent": "儀琳"})
        failures += not check("重傷者被補刀而死", not yilin.alive, yilin.wound_word)
        failures += not check("旁觀者見殺，義憤上升", lh.fury > fury_lh0,
                              f"{fury_lh0}→{lh.fury}")
        failures += not check("下手的人自己不義憤", fei.fury == fury_fei0)
        failures += not check("見血後旁觀者放下手邊的事重新決定", lh.action is None)

        print("\n背水一戰")
        # 重傷的令狐沖(6)硬拚沒受傷的費彬(8)，要有實質勝算——不再是必敗清場。
        # 舊公式下重傷 -4 讓弱者出手幾乎穩輸；背水一戰把傷勢對「攻擊」的拖累抵掉。
        hits = 0
        for tk in range(60):
            wd = jianghu.build_world("desp", 7)
            wd.tick = tk
            lhx, feix = wd.agents["linghu_chong"], wd.agents["fei_bin"]
            lhx.wound = 2
            feix.pos = Pos(lhx.pos.x + 1, lhx.pos.y)
            war_engine(wd)._apply_intent(lhx, {"kind": "attack", "target_agent": "費彬"})
            if feix.wound > 0:
                hits += 1
        failures += not check("重傷者拼死一擊能傷到武功更高的人", hits > 0, f"{hits}/60 命中")

        print("\n尋仇")
        # 有人死了，噩耗＋尋仇的念頭下一拍傳到不在場的知音眼前（obs.injected → needs_llm）。
        w = jianghu.build_world("revenge", 7)
        w.tick = 10
        director = Director(script=[], log=blood)
        fei, liu, qu = w.agents["fei_bin"], w.agents["liu_zhengfeng"], w.agents["qu_yang"]
        fei.fury = 6      # 墊高勝負讓這一刀穩死，好專心驗尋仇（fury 值本身不是重點）
        liu.wound = 2
        fei.pos = Pos(liu.pos.x + 1, liu.pos.y)
        qu.pos = Pos(12, 14)   # 曲洋在城外荒祠，看不見這一幕
        war_engine(w, director)._apply_intent(fei, {"kind": "attack", "target_agent": "劉正風"})
        failures += not check("劉正風被殺", not liu.alive, liu.wound_word)
        queued = [c for c in director.cues_for_tick(11) if c.get("agent") == "qu_yang"]
        failures += not check("噩耗下一拍注入不在場的知音",
                              len(queued) == 1 and "劉正風" in queued[0]["text"],
                              f"{len(queued)} 則")
        w.tick = 11
        inj = director.apply(w, jianghu.build_grid())
        failures += not check("尋仇注入送達知音的眼前",
                              any("劉正風" in x for x in inj.get("qu_yang", [])))

        # 沒有親友的人被殺，不會憑空冒出尋仇者——尋仇要有交情作根據。
        w2 = jianghu.build_world("norev", 7)
        w2.tick = 10
        d2 = Director(script=[], log=blood)
        fei2, yl2 = w2.agents["fei_bin"], w2.agents["yi_lin"]
        yl2.wound = 2
        yl2.pos = Pos(fei2.pos.x + 1, fei2.pos.y)
        war_engine(w2, d2)._apply_intent(fei2, {"kind": "attack", "target_agent": "儀琳"})
        failures += not check("儀琳被殺", not yl2.alive, yl2.wound_word)
        failures += not check("無親友者被殺不生尋仇注入",
                              d2.runtime_injections == [], str(d2.runtime_injections))

        # 親眼看見的親友不重複報信（義憤和記憶已經夠了）。
        w3 = jianghu.build_world("seen", 7)
        w3.tick = 10
        d3 = Director(script=[], log=blood)
        fei3, liu3, qu3 = w3.agents["fei_bin"], w3.agents["liu_zhengfeng"], w3.agents["qu_yang"]
        fei3.fury = 6
        liu3.wound = 2
        fei3.pos = Pos(liu3.pos.x + 1, liu3.pos.y)
        qu3.pos = Pos(liu3.pos.x, liu3.pos.y + 1)   # 曲洋就在旁邊，親眼看見
        war_engine(w3, d3)._apply_intent(fei3, {"kind": "attack", "target_agent": "劉正風"})
        failures += not check("親眼看見的親友不另外報信",
                              not d3.runtime_injections, str(d3.runtime_injections))

        # 序列化要涵蓋新欄位（fury / kin），否則分支重跑會丟掉義憤與交情。
        qu_rt = AgentState.from_dict(qu.to_dict())
        failures += not check("fury / kin 可序列化往返",
                              qu_rt.fury == qu.fury and qu_rt.kin == qu.kin,
                              f"fury={qu_rt.fury} kin={qu_rt.kin}")

        # ---- 記憶檢索 ----
        print("\n記憶檢索")
        p = engine.world.protagonist()
        got = p.memory.retrieve("咖啡館 梅姨", engine.world.tick, 5, engine.cfg)
        failures += not check("檢索回傳結果", len(got) > 0, f"{len(got)} 條")
        failures += not check("檢索不重複", len({m.id for m in got}) == len(got))

        print()
        if failures:
            print(f"✗ {failures} 項失敗")
        else:
            print("✓ 全數通過")
        return 1 if failures else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
