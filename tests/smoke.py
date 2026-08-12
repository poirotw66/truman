"""離線煙霧測試：不呼叫 API，驗證 tick 迴圈、intent 驗證、序列化、分支。

    python -m tests.smoke
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenarios import seahaven, jianghu  # noqa: E402
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

        # ---- 江湖劇本的公開常識 ----
        print("\n江湖劇本世界區塊")
        from scenarios import hakoniwa as hakoniwa_mod  # noqa: PLC0415
        from scenarios import jianghu as jianghu_mod  # noqa: PLC0415
        from truman import cli as cli_mod  # noqa: PLC0415
        from truman.world import arts as arts_mod_t  # noqa: PLC0415
        jh_grid = jianghu_mod.build_grid()
        jh_wb = world_block(
            jh_grid, jianghu_mod.BRIEF, jianghu_mod.NORMS, jianghu_mod.PUBLIC_CAST,
            setting=jianghu_mod.SETTING, examples=jianghu_mod.EXAMPLES,
            combat=jianghu_mod.COMBAT, arts=True,
        )
        # 招式的**名號**是公開常識，走 CLI 那條真正的組裝路徑來驗
        # （直接呼叫 world_block 而不給 arts_catalog 的話，這段本來就不會出現，
        #  而「大嵩陽手」「快刀」在 PUBLIC_CAST 裡本來就有——那樣測等於沒測）。
        jh_full = cli_mod.scenario_world_block(jianghu_mod, jh_grid)
        failures += not check("世界區塊有招式名號那一段",
                              "# 江湖上有名的功夫" in jh_full and "名正言順" in jh_full)
        failures += not check("同一門招式只列一次（順序一變快取就沒了）",
                              jh_full.count("- 打聽：") == 1)
        # 使用時機是給持有者的說明，屬於 system[1]。攤進共用區塊等於把底牌連用法一起發給所有人。
        failures += not check(
            "使用時機不進共用區塊",
            "什麼時候用" not in jh_full
            and arts_mod_t.CATALOG["ming_zheng_yan_shun"].when[:12] not in jh_full)
        # 誰配了什麼是私有的：世界區塊不該把人名和招式綁在一起
        one_line = jh_full.replace("\n", " ")
        import re as _re  # noqa: PLC0415
        failures += not check(
            "世界區塊沒把人名和招式綁在一起",
            not any(_re.search(f"{n}[^。]{{0,40}}{a}", one_line)
                    for n, a in (("費彬", "名正言順"), ("田伯光", "花言巧語"),
                                 ("曲洋", "廣陵散"))))
        # 劇本專屬的背景要留在劇本檔裡，不能寫死在共用的 prompts 模組
        failures += not check("公開背景由劇本提供", "PUBLIC_LORE" in dir(jianghu_mod)
                              and jianghu_mod.PUBLIC_LORE[:8] in jh_full)
        # 和平劇本不該收到任何武林設定——hakoniwa 之後配了絕技也一樣
        for peace in (seahaven, hakoniwa_mod):
            pw = cli_mod.scenario_world_block(peace, peace.build_grid())
            failures += not check(f"{peace.NAME} 不含武林設定",
                                  not any(x in pw for x in
                                          ("大嵩陽手", "五嶽劍派", "江湖上有名的功夫")))

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

        # 機制對了還不夠：角色要在「此刻」讀得到，否則永遠不會去用它。
        # j2 全程 13 次動手只有 1 次是帶傷者發動——因為 observation 只講傷的壞處。
        wo = jianghu.build_world("obs_wound", 7)
        wo.tick = 5
        who = wo.agents["linghu_chong"]
        grid_j = jianghu.build_grid()
        txt0 = build_observations(wo, grid_j, [], {}, SimConfig())[who.id].render()
        failures += not check("沒傷沒怒時完全不提（和平劇本讀起來一樣）",
                              "門戶" not in txt0 and "更狠" not in txt0)
        who.wound = 1
        txt1 = build_observations(wo, grid_j, [], {}, SimConfig())[who.id].render()
        failures += not check("輕傷要講守勢變弱", "防身" in txt1)
        failures += not check("輕傷不能只講壞處（攻擊還使得上力）", "還使得上力" in txt1)
        who.wound = 2
        txt2 = build_observations(wo, grid_j, [], {}, SimConfig())[who.id].render()
        failures += not check("重傷要講這一刀更狠", "更狠" in txt2)
        failures += not check("重傷也要講門戶大開（不能只講強）", "門戶大開" in txt2)

        # fury 先前只進骰子沒進眼前，角色不知道自己在憤怒（儀琳封頂 4 到死沒動手）
        wf = jianghu.build_world("obs_fury", 7)
        wf.tick = 5
        whf = wf.agents["yi_lin"]
        f0 = build_observations(wf, grid_j, [], {}, SimConfig())[whf.id].render()
        failures += not check("fury 0 時不提", "義憤" not in f0 and "這股火" not in f0)
        whf.fury = 2
        f2 = build_observations(wf, grid_j, [], {}, SimConfig())[whf.id].render()
        failures += not check("見過一次殺人：眼前讀得到", "比平時狠" in f2)
        whf.fury = 4
        f4 = build_observations(wf, grid_j, [], {}, SimConfig())[whf.id].render()
        failures += not check("義憤封頂時語氣更重", "不會再留餘地" in f4)

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

        # ---- 人物設定檔（--cast）----
        # 人物工作室產出的 JSON 是這次 run 的人物真相來源：套錯了會整場跑歪，
        # 所以驗證要在燒掉 API 額度之前就擋下壞檔案。
        print("\n人物設定檔")
        from truman import cast as cast_mod  # noqa: PLC0415

        jgrid = jianghu.build_grid()
        base_cast = {"scenario": "jianghu", "agents": [
            {"id": a["id"], "name": a["name"], "home_area": a["home_area"],
             "start": list(a["start"]), "skill": a["skill"], "kin": list(a.get("kin", [])),
             "persona": a["persona"], "public": f"- {a['name']}，測試用。"}
            for a in jianghu.AGENTS]}
        failures += not check("劇本原設定通過驗證",
                              cast_mod.validate(base_cast, jgrid, "jianghu") == [])

        broken = json.loads(json.dumps(base_cast))
        broken["agents"][0]["start"] = [0, 0]          # 城牆
        broken["agents"][1]["kin"] = ["nobody"]
        broken["agents"][2]["id"] = broken["agents"][3]["id"]
        broken["agents"][4]["persona"] = "  "
        broken["agents"][5]["skill"] = 99
        probs = cast_mod.validate(broken, jgrid, "jianghu")
        failures += not check("站不上去 / 重複 id / 空人設 / 亂指親友 / 武功超範圍都抓得到",
                              len(probs) >= 5, f"{len(probs)} 個問題")

        cw = jianghu.build_world("cast", 7)
        edited = json.loads(json.dumps(base_cast))
        edited["agents"] = [a for a in edited["agents"] if a["id"] != "tian_boguang"]
        for a in edited["agents"]:
            a["kin"] = [k for k in a["kin"] if k != "tian_boguang"]
            if a["id"] == "fei_bin":
                a["skill"] = 3
                a["persona"] = "換過的人設。"
                a["start"] = [9, 8]
        cast_mod.apply(cw, edited, jgrid)
        fb = cw.agents["fei_bin"]
        failures += not check("套用後人少一個", len(cw.agents) == 5 and "tian_boguang" not in cw.agents,
                              f"{len(cw.agents)} 人")
        failures += not check("套用後人設／武功／位置都換掉",
                              fb.persona == "換過的人設。" and fb.skill == 3 and fb.pos == Pos(9, 8),
                              f"skill={fb.skill} pos={fb.pos}")
        failures += not check("沒寫到的人沿用劇本預設",
                              cw.agents["liu_zhengfeng"].kin == ["qu_yang"])
        pub = cast_mod.public_cast_text(edited, jianghu.PUBLIC_CAST)
        failures += not check("公開人物表跟著換（拿掉的人不再被介紹）",
                              "田伯光" not in pub and "劉正風" in pub)
        failures += not check("空的設定檔擋下來",
                              _raises(lambda: cast_mod.load(tmp / "does_not_exist.json")))

        # 每個人可以自己掛模型／溫度：換一個人的腦袋是最乾淨的對照實驗
        bad_llm = json.loads(json.dumps(base_cast))
        bad_llm["agents"][0]["llm"] = {"temperature": 5}
        bad_llm["agents"][1]["llm"] = {"thinking": "very-high"}
        bad_llm["agents"][2]["llm"] = {"model": "gemini-does-not-exist"}
        lp = cast_mod.validate(bad_llm, jgrid, "jianghu", "gemini")
        failures += not check("溫度超範圍／thinking 打錯／模型不在目錄都抓得到",
                              len(lp) >= 3, f"{len(lp)} 個問題")

        ok_llm = json.loads(json.dumps(base_cast))
        ok_llm["agents"][1]["llm"] = {"model": "gemini-3.5-flash", "temperature": 0.2}
        failures += not check("合法的模型設定通過驗證",
                              cast_mod.validate(ok_llm, jgrid, "jianghu", "gemini") == [])
        w2 = jianghu.build_world("llm", 7)
        cast_mod.apply(w2, ok_llm, jgrid)
        fb2 = w2.agents["fei_bin"]
        failures += not check("模型設定套進 AgentState",
                              fb2.llm == {"model": "gemini-3.5-flash", "temperature": 0.2}, str(fb2.llm))
        failures += not check("沒設的人保持乾淨（照分層路由）",
                              w2.agents["liu_zhengfeng"].llm == {})
        failures += not check("llm 欄位可序列化往返",
                              AgentState.from_dict(fb2.to_dict()).llm == fb2.llm)

        # Call 帶得動覆寫，而且計價會分桶——不然自帶模型的呼叫會被按預設模型計價
        from truman.agents.cognition import agent_llm  # noqa: PLC0415
        from truman.llm.base import Call  # noqa: PLC0415

        failures += not check("agent.llm 轉成 Call 的覆寫欄位",
                              agent_llm(fb2) == {"model": "gemini-3.5-flash",
                                                 "temperature": 0.2, "thinking": None})
        cfg_g = SimConfig(provider="gemini")
        from truman.llm.base import BaseLLMClient  # noqa: PLC0415

        llm_c = BaseLLMClient(cfg_g, EventLog(tmp / "bucket"))   # 只用統計那半邊，不送請求
        default_model = cfg_g.models["routine"]
        failures += not check("同模型不分桶", llm_c._bucket("routine", default_model) == "routine")
        b2 = llm_c._bucket("routine", "gemini-3.5-flash")
        failures += not check("自帶模型另外分桶", b2 == "routine·gemini-3.5-flash", b2)
        failures += not check("分桶還原得回模型（計價用）",
                              llm_c._model_of(b2) == "gemini-3.5-flash"
                              and llm_c._model_of("routine") == default_model)
        # 分桶之後計價要用各自的模型價目，不能全部按分層預設算
        llm_c._usage(b2).add(inp=1_000_000, out=0)
        llm_c._usage("routine").add(inp=1_000_000, out=0)
        st = llm_c.stats()
        failures += not check("兩個桶各自用自己的模型計價",
                              st[b2]["model"] == "gemini-3.5-flash"
                              and st["routine"]["model"] == default_model
                              and st[b2]["cost_usd"] != st["routine"]["cost_usd"],
                              f"{st[b2]['cost_usd']} vs {st['routine']['cost_usd']}")
        c_ov = Call(key="k", tier="routine", system_blocks=["a"], user_message="u", schema={},
                    model="gemini-3.5-flash", temperature=0.2)
        failures += not check("Call 帶得動覆寫欄位",
                              c_ov.model == "gemini-3.5-flash" and c_ov.temperature == 0.2)

        # ---- 循序暖機：前綴進不了快取就別浪費那一趟來回 ----
        print("")
        print("批次排程")
        short = Call(key="s", tier="routine", system_blocks=["短前綴"], user_message="u", schema={})
        long_blocks = ["世界" * 4000]
        long_c = Call(key="l", tier="routine", system_blocks=long_blocks, user_message="u", schema={})
        m = cfg_g.models["routine"]
        failures += not check("前綴太短 → 不暖機（整批並行）",
                              llm_c._worth_warming(m, short) is False)
        failures += not check("前綴夠長 → 照舊暖機",
                              llm_c._worth_warming(m, long_c) is True)
        cfg_nc = SimConfig(provider="gemini", use_cache=False)
        llm_nc = BaseLLMClient(cfg_nc, EventLog(tmp / "nocache"))
        failures += not check("關掉快取 → 一律不暖機",
                              llm_nc._worth_warming(m, long_c) is False)

        # 真的有並行嗎：用會記錄「同時在跑幾個」的假 client 量一次
        class _Probe(BaseLLMClient):
            provider = "probe"

            def __init__(self, cfg, log):
                super().__init__(cfg, log)
                self.live = 0
                self.peak = 0

            async def _invoke(self, c, model):
                self.live += 1
                self.peak = max(self.peak, self.live)
                await asyncio.sleep(0.01)
                self.live -= 1
                return {"ok": True}, None, {"inp": 1, "out": 1}

        probe = _Probe(cfg_g, EventLog(tmp / "probe"))
        batch = [Call(key=f"k{i}", tier="routine", system_blocks=["短"], user_message="u",
                      schema={}) for i in range(6)]
        got = asyncio.run(probe.run_batch(batch))
        failures += not check("六個呼叫全部有結果", len(got) == 6)
        failures += not check("不暖機時六個一起送", probe.peak == 6, f"峰值併發 {probe.peak}")

        probe2 = _Probe(cfg_g, EventLog(tmp / "probe2"))
        batch2 = [Call(key=f"L{i}", tier="routine", system_blocks=long_blocks,
                       user_message="u", schema={}) for i in range(6)]
        asyncio.run(probe2.run_batch(batch2))
        failures += not check("要暖機時第一個獨自先跑", probe2.peak == 5, f"峰值併發 {probe2.peak}")

        # 每人自帶模型時要分組暖機：拿 A 模型暖機救不了 B 模型
        probe3 = _Probe(cfg_g, EventLog(tmp / "probe3"))
        mixed = [Call(key=f"m{i}", tier="routine", system_blocks=long_blocks, user_message="u",
                      schema={}, model=("gemini-3.5-flash" if i % 2 else None)) for i in range(6)]
        asyncio.run(probe3.run_batch(mixed))
        failures += not check("混模型時兩組各自暖機", probe3.peak == 4, f"峰值併發 {probe3.peak}")

        # ---- 逾時與重試：一條吊住的連線不該讓整場模擬停住（j2 卡了半小時的那個 bug）----
        print("")
        print("逾時與重試")
        cfg_t = SimConfig(provider="gemini", call_timeout=0.05, max_retries=3)

        class _Hang(BaseLLMClient):
            provider = "hang"

            def __init__(self, cfg, log):
                super().__init__(cfg, log)
                self.tries = 0

            async def _invoke(self, c, model):
                self.tries += 1
                await asyncio.sleep(60)   # 永遠不回來

        hang = _Hang(cfg_t, EventLog(tmp / "hang"))
        one = Call(key="h", tier="routine", system_blocks=["短"], user_message="u", schema={})
        t0 = time.monotonic()
        failures += not check("吊住的呼叫會逾時，不會永遠等下去",
                              _raises(lambda: asyncio.run(hang.call(one))))
        failures += not check("逾時會重試到上限就放棄", hang.tries == 3, f"送了 {hang.tries} 次")
        failures += not check("逾時不是靠等自然結束", time.monotonic() - t0 < 20,
                              f"{time.monotonic() - t0:.1f}s")

        class _Flaky(BaseLLMClient):
            provider = "flaky"

            def __init__(self, cfg, log):
                super().__init__(cfg, log)
                self.tries = 0

            async def _invoke(self, c, model):
                self.tries += 1
                if self.tries < 3:
                    raise RuntimeError("503 Service Unavailable")
                return {"ok": True}, None, {"inp": 1, "out": 1}

        flaky = _Flaky(cfg_t, EventLog(tmp / "flaky"))
        failures += not check("暫時性錯誤重試後救得回來",
                              asyncio.run(flaky.call(one)) == {"ok": True}
                              and flaky.tries == 3, f"送了 {flaky.tries} 次")

        class _Bad(BaseLLMClient):
            provider = "bad"

            def __init__(self, cfg, log):
                super().__init__(cfg, log)
                self.tries = 0

            async def _invoke(self, c, model):
                self.tries += 1
                raise ValueError("schema 不合法")

        bad = _Bad(cfg_t, EventLog(tmp / "bad"))
        failures += not check("非暫時性錯誤直接丟出",
                              _raises(lambda: asyncio.run(bad.call(one))))
        failures += not check("非暫時性錯誤不重試（不白花錢）", bad.tries == 1,
                              f"送了 {bad.tries} 次")

        # ---- 絕技（＝這個世界的 tool）----
        # 驗的是「工具契約」那幾件事：不在身上的用不出來、配額會扣、冷卻擋得住、
        # 距離不夠打不到、前提不成立就駁回、效果真的兌現。
        print("\n絕技")
        from truman.world import arts as arts_mod  # noqa: PLC0415
        from truman.world import goals as goals_mod  # noqa: PLC0415

        def art_world(tick: int = 0):
            w = jianghu.build_world("arts", 7)
            w.tick = tick
            return w, war_engine(w, run="arts")

        w, eng = art_world()
        fei = w.agents["fei_bin"]
        liu = w.agents["liu_zhengfeng"]
        failures += not check("劇本有替角色配上絕技", len(fei.arts) == 3,
                              str([x.id for x in fei.arts]))
        failures += not check("每個人都有明確的目的",
                              all(a.goals for a in w.agents.values()),
                              str({a.name: len(a.goals) for a in w.agents.values()}))

        # 沒配的功夫使不出來，而且駁回理由要列出他實際會的——不然他會一直猜。
        eng._apply_intent(fei, {"kind": "use_art", "art": "獨孤九劍"})
        failures += not check("沒學過的絕技使不出來",
                              "大嵩陽手" in fei.last_rejection, fei.last_rejection)

        # 配額：大嵩陽手三次、冷卻 6 拍。
        slot = fei.art("da_song_yang")
        eng._apply_intent(fei, {"kind": "use_art", "art": "大嵩陽手"})
        failures += not check("使出絕技會扣配額", slot.uses_left == 2, str(slot.uses_left))
        failures += not check("絕技效果真的掛上身", fei.buff("atk", 0) == 3,
                              str(fei.buffs))
        fei.last_rejection = ""
        eng._apply_intent(fei, {"kind": "use_art", "art": "大嵩陽手"})
        failures += not check("冷卻中使不出來",
                              "緩一緩" in fei.last_rejection and slot.uses_left == 2,
                              fei.last_rejection)

        # 加成要真的進戰鬥判定，否則絕技就只是文字。
        w2, eng2 = art_world(tick=3)
        fb, yl = w2.agents["fei_bin"], w2.agents["yi_lin"]
        yl.pos = Pos(fb.pos.x + 1, fb.pos.y)
        base_margin = []
        eng2.log = type("L", (), {"write": lambda s, k, d: base_margin.append(d)
                                  if k == "attack" else None})()
        eng2._apply_intent(fb, {"kind": "attack", "target_agent": "儀琳"})
        w3, eng3 = art_world(tick=3)
        fb3, yl3 = w3.agents["fei_bin"], w3.agents["yi_lin"]
        yl3.pos = Pos(fb3.pos.x + 1, fb3.pos.y)
        buffed = []
        eng3.log = type("L", (), {"write": lambda s, k, d: buffed.append(d)
                                  if k == "attack" else None})()
        fb3.buffs["atk"] = {"amount": 3, "until": 99}
        eng3._apply_intent(fb3, {"kind": "attack", "target_agent": "儀琳"})
        failures += not check("攻勢加成真的進了戰鬥判定",
                              buffed[0]["margin"] == base_margin[0]["margin"] + 3,
                              f"{base_margin[0]['margin']} → {buffed[0]['margin']}")

        # 距離：名正言順要在聽力範圍內才使得出來。
        w4, eng4 = art_world()
        fb4, liu4 = w4.agents["fei_bin"], w4.agents["liu_zhengfeng"]
        liu4.pos = Pos(fb4.pos.x + 9, fb4.pos.y)
        eng4._apply_intent(fb4, {"kind": "use_art", "art": "名正言順",
                                 "target_agent": "劉正風"})
        failures += not check("對象太遠就使不出來",
                              "使不出" in fb4.last_rejection
                              and fb4.art("ming_zheng_yan_shun").uses_left == 1,
                              fb4.last_rejection)

        # 前提：酒膽只在帶傷時使得上。
        w5, eng5 = art_world()
        lh5 = w5.agents["linghu_chong"]
        eng5._apply_intent(lh5, {"kind": "use_art", "art": "酒膽"})
        failures += not check("前提不成立就駁回（沒傷不能喝酒膽）",
                              "用不著" in lh5.last_rejection
                              and lh5.art("jiu_dan").uses_left == 2, lh5.last_rejection)
        lh5.wound = 1
        lh5.last_rejection = ""
        eng5._apply_intent(lh5, {"kind": "use_art", "art": "酒膽"})
        failures += not check("帶傷就使得出來",
                              lh5.buff("atk", 0) == 4 and lh5.art("jiu_dan").uses_left == 1,
                              str(lh5.buffs))

        # ---- 絕技怎麼推動目的 ----
        print("\n絕技推動目的")
        # 金盆洗手：要在劉府、要有人觀禮，辦成了 ritual 目的就結案。
        w6, eng6 = art_world()
        liu6, fei6 = w6.agents["liu_zhengfeng"], w6.agents["fei_bin"]
        fei6.pos = Pos(20, 6)  # 先把人支開，湊不足觀禮的人
        for other in w6.agents.values():
            if other is not liu6:
                other.pos = Pos(20, 6)
        eng6._apply_intent(liu6, {"kind": "use_art", "art": "金盆洗手"})
        failures += not check("沒人觀禮就辦不成",
                              "沒有人" in liu6.last_rejection
                              and liu6.art("jin_pen_xi_shou").uses_left == 1,
                              liu6.last_rejection)
        fei6.pos = Pos(liu6.pos.x + 1, liu6.pos.y)
        liu6.last_rejection = ""
        eng6._apply_intent(liu6, {"kind": "use_art", "art": "金盆洗手"})
        failures += not check("有人觀禮就辦得成",
                              "金盆洗手" in eng6._signals.rites.get("liu_zhengfeng", []),
                              str(eng6._signals.rites))
        goals_mod.evaluate(w6, jianghu.build_grid(), SimConfig(combat=True),
                           eng6._signals)
        failures += not check("辦成了，ritual 目的就結案",
                              liu6.goals[0].status == "done", liu6.goals[0].note)
        failures += not check("對手的 prevent 目的跟著失敗",
                              fei6.goals[0].status == "failed", fei6.goals[0].note)

        # 名正言順：說破了，被指的人 conceal 目的失敗。
        w7, eng7 = art_world()
        fb7, qu7 = w7.agents["fei_bin"], w7.agents["qu_yang"]
        qu7.pos = Pos(fb7.pos.x + 1, fb7.pos.y)
        eng7._apply_intent(fb7, {"kind": "use_art", "art": "名正言順",
                                 "target_agent": "曲洋",
                                 "utterance": "「這位可是日月神教的曲長老。」"})
        goals_mod.evaluate(w7, jianghu.build_grid(), SimConfig(combat=True),
                           eng7._signals)
        failures += not check("當眾說破，conceal 目的失敗",
                              qu7.goals[0].status == "failed", qu7.goals[0].note)

        # 隱匿行藏擋得掉一次，而且擋完就沒了。
        w8, eng8 = art_world()
        fb8, qu8 = w8.agents["fei_bin"], w8.agents["qu_yang"]
        qu8.pos = Pos(fb8.pos.x + 1, fb8.pos.y)
        eng8._apply_intent(qu8, {"kind": "use_art", "art": "隱匿行藏"})
        eng8._apply_intent(fb8, {"kind": "use_art", "art": "名正言順",
                                 "target_agent": "曲洋", "utterance": "「他是魔教的。」"})
        goals_mod.evaluate(w8, jianghu.build_grid(), SimConfig(combat=True),
                           eng8._signals)
        failures += not check("隱匿行藏擋得掉一次指證",
                              qu8.goals[0].status == "open", qu8.goals[0].note)
        failures += not check("擋掉之後隱匿就失效了", qu8.buff("veil", 0) == 0,
                              str(qu8.buffs))
        failures += not check("被擋掉也照樣消耗指證的配額",
                              fb8.art("ming_zheng_yan_shun").uses_left == 0)

        # 輕功：腳程真的加倍。
        w9, eng9 = art_world()
        yl9 = w9.agents["yi_lin"]
        yl9.action = {"kind": "move_to", "target_area": "城門",
                      "path": [[x, 10] for x in range(1, 13)], "done": False}
        eng9._advance(yl9)
        plain = 12 - len(yl9.action["path"])
        w10, eng10 = art_world()
        yl10 = w10.agents["yi_lin"]
        yl10.buffs["dash"] = {"amount": 2, "until": 99}
        yl10.action = {"kind": "move_to", "target_area": "城門",
                       "path": [[x, 10] for x in range(1, 13)], "done": False}
        eng10._advance(yl10)
        dashed = 12 - len(yl10.action["path"])
        failures += not check("輕功讓腳程加倍", dashed == plain * 2,
                              f"{plain} 格 → {dashed} 格")

        # ---- 目的判定器 ----
        print("\n目的判定")
        cfgw = SimConfig(combat=True)
        gridw = jianghu.build_grid()

        w11, _ = art_world()
        yl11 = w11.agents["yi_lin"]
        yl11.pos = Pos(21, 6)  # 城門
        goals_mod.evaluate(w11, gridw, cfgw, goals_mod.Signals.empty(1))
        failures += not check("走到目的地就達成 reach",
                              yl11.goals[0].status == "done", yl11.goals[0].note)

        w12, _ = art_world()
        yl12, lh12 = w12.agents["yi_lin"], w12.agents["linghu_chong"]
        yl12.wound = 3
        goals_mod.evaluate(w12, gridw, cfgw, goals_mod.Signals.empty(1))
        failures += not check("要護的人死了，protect 就失敗",
                              lh12.goals[0].status == "failed", lh12.goals[0].note)
        failures += not check("人死了，他自己的目的一律失敗",
                              all(g.status == "failed" for g in yl12.goals),
                              str([g.note for g in yl12.goals]))

        # isolate：同一個僻靜處、而且沒有第三個活人看得見。
        w13, _ = art_world()
        tb13, yl13, lh13 = (w13.agents["tian_boguang"], w13.agents["yi_lin"],
                            w13.agents["linghu_chong"])
        for other in w13.agents.values():  # 先把不相干的人清出視野（曲洋起始就在荒祠）
            other.pos = Pos(1, 1)
        tb13.pos, yl13.pos = Pos(12, 14), Pos(13, 14)  # 荒祠
        lh13.pos = Pos(13, 13)  # 就在旁邊看著
        goals_mod.evaluate(w13, gridw, cfgw, goals_mod.Signals.empty(1))
        failures += not check("有人看著就不算把人帶走了",
                              tb13.goals[0].status == "open")
        lh13.pos = Pos(1, 1)
        goals_mod.evaluate(w13, gridw, cfgw, goals_mod.Signals.empty(2))
        failures += not check("沒人看見才算 isolate 達成",
                              tb13.goals[0].status == "done", tb13.goals[0].note)

        # ritual 有時限，過了就沒指望。
        w14, _ = art_world()
        liu14, fei14 = w14.agents["liu_zhengfeng"], w14.agents["fei_bin"]
        goals_mod.evaluate(w14, gridw, cfgw, goals_mod.Signals.empty(61))
        failures += not check("過了時辰，ritual 目的失敗",
                              liu14.goals[0].status == "failed", liu14.goals[0].note)
        failures += not check("拖過時辰，費彬的 prevent 就達成",
                              fei14.goals[0].status == "done", fei14.goals[0].note)

        # 收工結算：主動目的沒做到算失敗，被動目的沒出事算達成。
        w15, _ = art_world()
        goals_mod.finalize(w15, gridw, cfgw, 96)
        failures += not check("收工時主動目的沒做到就是失敗",
                              w15.agents["yi_lin"].goals[0].status == "failed")
        failures += not check("收工時被動目的沒出事就是達成",
                              w15.agents["yi_lin"].goals[1].status == "done")
        failures += not check("結了案就不會再被翻盤",
                              all(not g.open for a in w15.agents.values()
                                  for g in a.goals))
        # 迴歸：收工時 prevent 要在對手結案之後才判，否則「他沒洗成」和「我沒攔住」
        # 會同時成立，報表上兩個人都失敗。
        failures += not check("收工時洗手沒辦成，攔阻的人就算贏",
                              w15.agents["liu_zhengfeng"].goals[0].status == "failed"
                              and w15.agents["fei_bin"].goals[0].status == "done",
                              w15.agents["fei_bin"].goals[0].note)

        # ---- 序列化：目的與絕技要撐得過 checkpoint / fork ----
        print("\n目的與絕技的序列化")
        w16, eng16 = art_world()
        fb16 = w16.agents["fei_bin"]
        eng16._apply_intent(fb16, {"kind": "use_art", "art": "大嵩陽手"})
        fb16.goals[0].status = "done"
        fb16.goals[0].note = "測試"
        back = WorldState.from_dict(json.loads(json.dumps(w16.to_dict())))
        fb17 = back.agents["fei_bin"]
        failures += not check("目的序列化往返",
                              fb17.goals[0].status == "done" and fb17.goals[0].note == "測試")
        failures += not check("絕技的剩餘次數與冷卻撐得過往返",
                              fb17.art("da_song_yang").uses_left == 2
                              and fb17.art("da_song_yang").ready_at
                              == fb16.art("da_song_yang").ready_at)
        failures += not check("絕技效果撐得過往返", fb17.buff("atk", 0) == 3)
        old = {k: v for k, v in w16.agents["yi_lin"].to_dict().items()
               if k not in ("goals", "arts", "buffs")}
        failures += not check("舊 checkpoint（沒有這三個欄位）照樣讀得回來",
                              AgentState.from_dict(old).goals == []
                              and AgentState.from_dict(old).arts == [])

        # ---- 絕技與目的怎麼進 prompt ----
        print("\n絕技與目的進 prompt")
        from truman.llm.prompts import persona_block as pb  # noqa: PLC0415

        blk = pb(w16.agents["tian_boguang"])
        failures += not check("角色看得到自己的目的",
                              "把那個小尼姑帶到沒有人的地方" in blk)
        failures += not check("角色看得到自己會的絕技與使用時機",
                              "花言巧語" in blk and "什麼時候用" in blk)
        failures += not check("看不到別人的絕技", "廣陵散" not in blk)
        failures += not check("配額不進 system[1]（那會每 tick 打掉快取前綴）",
                              "還能使" not in blk)

        w18 = seahaven.build_world("nop", 1)
        failures += not check("沒配絕技的人整段不存在",
                              "你會的絕技" not in pb(w18.protagonist())
                              and "你會的絕技" in blk)
        failures += not check("沒有目的的人整段不存在",
                              "你今天要做到的事" not in pb(w18.protagonist()))

        arts_kinds = action_schema(True, True)["properties"]["action"]["properties"]
        failures += not check("use_art 只出現在有絕技的人的 schema",
                              "use_art" in arts_kinds["kind"]["enum"]
                              and "use_art" not in action_schema(True, False)
                              ["properties"]["action"]["properties"]["kind"]["enum"])
        failures += not check("絕技規則只進有絕技的劇本的世界區塊",
                              "use_art" in cli_mod.scenario_world_block(
                                  jianghu, jianghu.build_grid())
                              and "use_art" not in cli_mod.scenario_world_block(
                                  seahaven, grid))

        # 配額與目的進度走 observation 這一層（每 tick 都變的東西只能放這裡）。
        obs_a = build_observations(w16, jianghu.build_grid(), [], {}, cfgw)
        rendered = obs_a["fei_bin"].render()
        # w16 的費彬剛使過大嵩陽手，所以這裡看到的是「還剩幾次」與「還要幾刻」兩種狀態。
        failures += not check("剩餘次數每 tick 出現在眼前",
                              "打聽（還能使 4 次）" in rendered, rendered[-400:])
        failures += not check("冷卻中的絕技會標明還要多久",
                              "大嵩陽手（還要 6 刻才緩得過來）" in rendered, rendered[-400:])
        failures += not check("身上還在的效果看得見", "出手更重" in rendered)
        failures += not check("目的進度看得見", "已經做到" in rendered)

        # ---- 設定檔驗證 ----
        print("\n設定檔裡的目的與絕技")
        bad_cast = {"scenario": "jianghu", "agents": [
            {"id": "a", "name": "甲", "persona": "測試", "start": [1, 1],
             "arts": ["不存在的絕技"],
             "goals": [{"kind": "reach", "text": "去", "params": {"area": "台北"}},
                       {"kind": "protect", "text": "護", "params": {"who": ["查無此人"]}},
                       {"kind": "亂寫", "text": "亂"}]},
        ]}
        probs = cast_mod.validate(bad_cast, jianghu.build_grid(), "jianghu")
        failures += not check("絕技 id 打錯字會被擋下",
                              any("絕技目錄裡沒有" in p for p in probs))
        failures += not check("目的指到不存在的區域會被擋下",
                              any("不是這張地圖上的區域" in p for p in probs))
        failures += not check("目的指到不存在的人會被擋下",
                              any("不在這份名單裡" in p for p in probs))
        failures += not check("不認得的判定器會被擋下",
                              any("不認得的判定器" in p for p in probs))

        good_cast = {"scenario": "jianghu", "agents": [
            {"id": "fei_bin", "name": "費彬", "persona": "測試", "start": [5, 5],
             "arts": ["kuai_dao"], "goals": [{"kind": "survive", "text": "活著"}]},
        ]}
        failures += not check("合法的目的與絕技不會誤報",
                              cast_mod.validate(good_cast, jianghu.build_grid(),
                                                "jianghu") == [])
        w19 = jianghu.build_world("cast", 7)
        cast_mod.apply(w19, good_cast, jianghu.build_grid())
        failures += not check("設定檔換得掉絕技",
                              [x.id for x in w19.agents["fei_bin"].arts] == ["kuai_dao"])
        failures += not check("設定檔換得掉目的",
                              [g.kind for g in w19.agents["fei_bin"].goals] == ["survive"])

        # ---- 絕技走完一整個 tick 迴圈 ----
        # 前面都是直接戳 _apply_intent。這一段用一個照劇本回答的假 LLM 跑真的
        # engine.tick()，驗的是 _apply_intent 單獨測不到的東西：每拍的訊號有沒有
        # 重置、目的判定有沒有在迴圈裡跑、被指證的人有沒有拿到同一拍的回話機會。
        print("\n絕技跑完整個 tick")

        class _Scripted:
            """照 {tick: {agent: action}} 回答，其餘一律 wait。"""

            provider = "scripted"

            def __init__(self, script):
                self.script = script
                self.seen: list[str] = []

            def stats(self):
                return {"_provider": "scripted", "_total_cost_usd": 0.0}

            def total_cost(self):
                return 0.0

            async def run_batch(self, calls):
                out = {}
                for c in calls:
                    tick, who = int(c.key.split(":")[0]), c.key.split(":")[1]
                    self.seen.append(c.key)
                    if c.key.endswith(":reflect"):
                        out[c.key] = {"insights": [], "beliefs": []}
                        continue
                    act = self.script.get(tick, {}).get(who) or {
                        "kind": "wait", "target_area": "", "target_agent": "",
                        "utterance": "", "object": "", "art": "",
                    }
                    out[c.key] = {"thought": "（測試）", "action": act, "plan": "（測試）"}
                return out

        w20 = jianghu.build_world("loop", 7)
        liu20, fei20 = w20.agents["liu_zhengfeng"], w20.agents["fei_bin"]
        fei20.pos = Pos(liu20.pos.x + 1, liu20.pos.y)  # 費彬就在廳上
        elog = EventLog(tmp / "loop")
        elog.bind_tick(lambda: w20.tick)
        scripted = _Scripted({
            0: {"fei_bin": {"kind": "use_art", "art": "大嵩陽手", "target_area": "",
                            "target_agent": "", "utterance": "", "object": ""}},
            1: {"liu_zhengfeng": {"kind": "use_art", "art": "金盆洗手",
                                  "target_area": "", "target_agent": "",
                                  "utterance": "", "object": ""}},
        })
        eng20 = Engine(
            world=w20, grid=jianghu.build_grid(), cfg=SimConfig(combat=True),
            llm=scripted, director=Director(script=[], log=elog), log=elog,
            world_block_text="", run_dir=tmp / "loop",
        )
        asyncio.run(eng20.tick())
        failures += not check("tick 迴圈裡使得出絕技",
                              fei20.art("da_song_yang").uses_left == 2
                              and fei20.buff("atk", 0) == 3, str(fei20.buffs))
        asyncio.run(eng20.tick())
        failures += not check("tick 迴圈裡辦成的儀式會結案",
                              liu20.goals[0].status == "done", liu20.goals[0].note)
        failures += not check("對手的目的在同一拍跟著失敗",
                              fei20.goals[0].status == "failed", fei20.goals[0].note)
        failures += not check("每拍的訊號會重置（上一拍的儀式不會重複計）",
                              eng20._signals.tick == 1
                              and "liu_zhengfeng" in eng20._signals.rites)
        asyncio.run(eng20.tick())
        failures += not check("下一拍訊號清空", eng20._signals.rites == {},
                              str(eng20._signals.rites))
        failures += not check("結案的目的會寫進當事人的記憶",
                              any("這件事了了" in m.content
                                  for m in liu20.memory.entries[-12:]))
        elog.close()
        loop_events = list(EventLog.read(tmp / "loop"))
        failures += not check("art_used 有寫進日誌",
                              any(e["type"] == "art_used" for e in loop_events))
        failures += not check("goal_done 有寫進日誌",
                              any(e["type"] == "goal_done" for e in loop_events))

        # 指證要走 speech 那條路，被指的人才有機會在同一拍回嘴。
        w21 = jianghu.build_world("loop2", 7)
        fei21, qu21 = w21.agents["fei_bin"], w21.agents["qu_yang"]
        qu21.pos = Pos(fei21.pos.x + 1, fei21.pos.y)
        elog2 = EventLog(tmp / "loop2")
        elog2.bind_tick(lambda: w21.tick)
        sc2 = _Scripted({0: {"fei_bin": {
            "kind": "use_art", "art": "名正言順", "target_agent": "曲洋",
            "utterance": "「這位是日月神教的曲長老。」", "target_area": "", "object": ""}}})
        eng21 = Engine(
            world=w21, grid=jianghu.build_grid(), cfg=SimConfig(combat=True),
            llm=sc2, director=Director(script=[], log=elog2), log=elog2,
            world_block_text="", run_dir=tmp / "loop2",
        )
        asyncio.run(eng21.tick())
        failures += not check("當眾指證讓被指的人當場有機會回話",
                              any(k.endswith("qu_yang:reply") for k in sc2.seen),
                              str([k for k in sc2.seen if "reply" in k]))
        failures += not check("指證在同一拍就打掉對方的 conceal 目的",
                              qu21.goals[0].status == "failed", qu21.goals[0].note)
        elog2.close()

        # ---- 全滅要提早收手 ----
        # 由 j3 逼出來的：96 拍、576 次呼叫全部失敗（2.5-flash-lite 不吃
        # thinking_level=medium），32 秒「跑完」，零對話零意圖，而且要等全部跑完才回報。
        print("\n全滅時的 fail-fast")

        class _AlwaysFails:
            provider = "broken"

            def stats(self):
                return {"_provider": "broken", "_total_cost_usd": 0.0}

            def total_cost(self):
                return 0.0

            async def run_batch(self, calls):
                return {c.key: RuntimeError("400 thinking level 不合") for c in calls}

        wf = jianghu.build_world("faildemo", 7)
        flog = EventLog(tmp / "faildemo")
        flog.bind_tick(lambda: wf.tick)
        engf = Engine(
            world=wf, grid=jianghu.build_grid(),
            cfg=SimConfig(combat=True, abort_after_failures=12),
            llm=_AlwaysFails(), director=Director(script=[], log=flog), log=flog,
            world_block_text="", run_dir=tmp / "faildemo",
        )
        for _ in range(10):
            if engf.aborted:
                break
            asyncio.run(engf.tick())
        failures += not check("全部呼叫失敗就中止", engf.aborted, engf.abort_reason[:60])
        failures += not check("中止得夠早（沒有把 10 拍跑完）", wf.tick < 10,
                              f"停在第 {wf.tick} 拍")
        failures += not check("中止有寫進日誌",
                              any(e["type"] == "run_aborted"
                                  for e in EventLog.read(tmp / "faildemo")))
        flog.close()

        # 只要成功過一次就不該中止——跑到一半遇到壞天氣是重試那層的事。
        class _FailsAfterOne(_AlwaysFails):
            def __init__(self):
                self.n = 0

            async def run_batch(self, calls):
                out = {}
                for c in calls:
                    self.n += 1
                    out[c.key] = ({"thought": "好", "plan": "好",
                                   "action": {"kind": "wait", "target_area": "",
                                              "target_agent": "", "utterance": "",
                                              "object": "", "art": ""}}
                                  if self.n == 1 else RuntimeError("503"))
                return out

        wg = jianghu.build_world("flaky", 7)
        glog = EventLog(tmp / "flaky")
        glog.bind_tick(lambda: wg.tick)
        engg = Engine(
            world=wg, grid=jianghu.build_grid(),
            cfg=SimConfig(combat=True, abort_after_failures=12),
            llm=_FailsAfterOne(), director=Director(script=[], log=glog), log=glog,
            world_block_text="", run_dir=tmp / "flaky",
        )
        for _ in range(6):
            asyncio.run(engg.tick())
        failures += not check("成功過就不中止（壞天氣交給重試那層）",
                              not engg.aborted and engg.failed_calls > 12,
                              f"失敗 {engg.failed_calls} 次仍繼續")
        glog.close()

        # 開跑前的試探呼叫：把每一組（模型, thinking）各送一次，錯的當場報出來。
        class _PickyModel(BaseLLMClient):
            provider = "picky"
            cache_write_multiplier = 0.0

            async def _invoke(self, c, model):
                if (c.thinking or "") == "medium":
                    raise ValueError("'medium' is not a supported thinking level")
                return {"ok": "1"}, None, {"inp": 1, "out": 1}

        cfg_p = SimConfig(provider="gemini")
        picky = _PickyModel(cfg_p, EventLog(tmp / "pf"))
        failures += not check("設定沒問題時開跑前檢查會過",
                              asyncio.run(picky.preflight()) == [])
        w_p = jianghu.build_world("pf", 7)
        w_p.agents["fei_bin"].llm = {"thinking": "medium"}
        bad_pf = asyncio.run(picky.preflight(w_p))
        failures += not check("agent 自帶的壞設定會在開跑前被抓到",
                              len(bad_pf) == 1 and bad_pf[0][1] == "medium",
                              str(bad_pf))
        failures += not check("replay 模式不做開跑前檢查（不會呼叫任何東西）",
                              asyncio.run(
                                  _PickyModel(cfg_p, EventLog(tmp / "pf2"),
                                              replay={}).preflight(w_p)) == [])

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
