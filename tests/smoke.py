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
from truman.world.state import WorldState  # noqa: E402

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
