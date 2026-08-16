"""CLI：跑模擬、重放、分支、出報告。

  python -m truman.cli --scenario jianghu run --ticks 96 --run-id j2
  python -m truman.cli replay  --run-id j2
  python -m truman.cli fork    --from-latest j2 --run-id j2_x --ticks 24 \\
                               --inject "liu_zhengfeng:（廳外忽然傳來一陣急驟的馬蹄聲。）"
  python -m truman.cli report  --run-id j2
  python -m truman.cli map
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .config import DEFAULT_PROVIDER, PROVIDERS, SimConfig, clock_str
from .director.director import Director
from .llm.client import build_replay_index, make_client
from .llm.prompts import world_block
from .obs import checkpoint
from .obs.eventlog import EventLog
from .world.engine import Engine
from .world.grid import Pos

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
console = Console()


def load_scenario(name: str):
    return importlib.import_module(f"scenarios.{name}")


def build_config(args, **kw) -> SimConfig:
    """套用 --model 覆寫。接受 `ID`（全層）或 `tier=ID`（單層），可重複。"""
    cfg = SimConfig(provider=args.provider, **kw)
    lvl = getattr(args, "thinking", None)
    if lvl:
        cfg.gemini_thinking = {t: lvl for t in cfg.models}
    for spec in getattr(args, "model", None) or []:
        tier, sep, model = spec.partition("=")
        if not sep:
            cfg.models = {t: spec for t in cfg.models}
        elif tier in cfg.models:
            cfg.models[tier] = model
        else:
            console.print(
                f"[red]--model {spec}：未知的層 {tier!r}，可選 {sorted(cfg.models)}[/red]"
            )
            sys.exit(2)
    return cfg


CRED_HINT = {
    "anthropic": "設 ANTHROPIC_API_KEY（或寫進專案根目錄的 .env），或執行 `ant auth login`。",
    "gemini": "設 GEMINI_API_KEY 或 GOOGLE_API_KEY（或寫進 .env）。",
}


_AUTH_HINTS = ("api_key", "api key", "credential", "authent", "permission", "401", "403")


def _looks_like_auth(e: Exception) -> bool:
    return any(h in f"{type(e).__name__}: {e}".lower() for h in _AUTH_HINTS)


def require_credentials(provider: str) -> None:
    """在燒掉任何時間之前先確認拿得到憑證。"""
    try:
        if provider == "anthropic":
            from anthropic import AsyncAnthropic

            AsyncAnthropic()
        elif provider == "gemini":
            from google import genai

            genai.Client()
        else:
            raise ValueError(f"未知的 provider: {provider}")
    except ImportError as e:
        console.print(
            f"[red]{provider} 的 SDK 沒裝：{e}[/red]\n"
            "  pip install anthropic          # Anthropic\n"
            "  pip install google-genai       # Gemini"
        )
        sys.exit(2)
    except Exception:  # noqa: BLE001 - 各家缺憑證時丟的例外型別不同
        console.print(
            f"[red]找不到 {provider} 的憑證。[/red]\n"
            f"  {CRED_HINT.get(provider, '')}\n"
            "  想先看看流程而不花錢，可以跑 `python -m tests.smoke`，"
            "或在 run/fork 加上 --stub。"
        )
        sys.exit(2)


def scenario_world_block(scen, grid, public_cast: str | None = None,
                         arts: bool | None = None) -> str:
    """劇本可以換掉世界設定與少樣例示範；機制與語氣是全劇本共用的。

    `public_cast` 是給 --cast 用的：換了人，公開人物表也要跟著換，
    否則世界區塊還在介紹一批已經不存在的人。

    `arts` 決定要不要把絕技那段規則掛上去。預設看劇本有沒有人配了絕技；
    --cast 可能把絕技全拿掉或全加上，所以呼叫端可以直接覆寫。
    """
    from .llm.prompts import SEAHAVEN_EXAMPLES, SEAHAVEN_SETTING

    from .world import arts as arts_mod

    if arts is None:
        arts = any(a.get("arts") for a in getattr(scen, "AGENTS", []))
    return world_block(
        grid,
        scen.BRIEF,
        scen.NORMS,
        public_cast if public_cast is not None else getattr(scen, "PUBLIC_CAST", ""),
        setting=getattr(scen, "SETTING", SEAHAVEN_SETTING),
        examples=getattr(scen, "EXAMPLES", SEAHAVEN_EXAMPLES),
        combat=getattr(scen, "COMBAT", False),
        arts=arts,
        # 只列這個劇本真的用得到的招式。和平劇本配了社交類絕技的話，
        # 名號那一段就只會出現社交類，不會冒出一堆刀劍。
        #
        # 刻意取自劇本模組而**不是**這場的實際世界，即使 --cast 換過配裝也一樣。
        # 兩個理由，都比「跟著 cast 走」重要：
        #   1. 這一段講的是「江湖上有名的功夫」，不是「今天在場的人會什麼」。
        #      費彬沒來，大嵩陽手照樣是江湖上的名號。跟著 cast 走反而會洩漏
        #      今天到底來了誰。
        #   2. 前綴要在不同 cast 之間保持一模一樣。換配裝跑對照實驗時，
        #      system[0] 不變、只有 system[1] 變——快取才共用得到，比較也才乾淨。
        # 去重要保順序：這一段進的是快取前綴，順序一變前綴就換了一份，
        # 快取直接 0 命中。dict.fromkeys 比 set 可靠。
        arts_catalog=[
            arts_mod.get(x) for x in dict.fromkeys(
                x for spec in getattr(scen, "AGENTS", []) for x in spec.get("arts", [])
            ) if arts_mod.get(x) is not None
        ],
        lore=getattr(scen, "PUBLIC_LORE", ""),
    )


def apply_cast(world, scen, path: str, provider: str | None = None):
    """讀人物設定檔、驗證、套進世界。回傳要覆蓋的公開人物表。"""
    from . import cast as cast_mod

    grid = scen.build_grid()
    try:
        data = cast_mod.load(path)
    except cast_mod.CastError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)
    problems = cast_mod.validate(data, grid, getattr(scen, "NAME", None), provider)
    if problems:
        console.print(f"[red]人物設定檔有 {len(problems)} 個問題，這次 run 不會開始：[/red]")
        for p in problems:
            console.print(f"  [red]· {p}[/red]")
        sys.exit(2)
    cast_mod.apply(world, data, grid)
    names = "、".join(a.name for a in world.agents.values())
    console.print(f"[dim]套用人物設定 {path}：{len(world.agents)} 人（{names}）[/dim]")
    for a in world.agents.values():
        if a.llm:
            bits = "、".join(f"{k}={v}" for k, v in a.llm.items())
            console.print(f"[dim]  · {a.name} 自帶模型設定：{bits}[/dim]")
    return cast_mod.public_cast_text(data, getattr(scen, "PUBLIC_CAST", ""))


def make_engine(world, scen, cfg, run_dir: Path, replay_index=None, quiet=False, stub=False,
                public_cast: str | None = None):
    grid = scen.build_grid()
    log = EventLog(run_dir)
    log.bind_tick(lambda: world.tick)
    if stub:
        from .llm.stub import StubLLM

        llm = StubLLM(cfg=cfg, log=log)
    else:
        llm = make_client(cfg=cfg, log=log, replay=replay_index)
    director = Director(script=list(scen.DIRECTOR_SCRIPT), log=log)
    # 舊 checkpoint 裡的淹水格要覆寫回地圖，否則 fork／續跑會變成乾的。
    for xy in getattr(world, "flooded", []) or []:
        grid.flooded.add(Pos(int(xy[0]), int(xy[1])))
    engine = Engine(
        world=world,
        grid=grid,
        cfg=cfg,
        llm=llm,
        director=director,
        log=log,
        # 絕技那段規則看的是「這場實際上有沒有人配了絕技」，不是劇本預設——
        # --cast 可能把絕技全拿掉，那就不該讓任何人看到 use_art。
        world_block_text=scenario_world_block(
            scen, grid, public_cast,
            arts=any(a.arts for a in world.agents.values()),
        ),
        run_dir=run_dir,
        console=None if quiet else console,
        on_after_goals=getattr(scen, "after_goals", None),
    )
    return engine, log, llm


async def _drive(engine, log, llm, ticks: int, quiet: bool) -> int:
    """回傳結束碼。全部呼叫都失敗時回 1——這種 run 不該看起來像成功。"""
    # 開跑前先驗設定。這一步擋掉的是 j3 那種「跑完 96 拍才發現每一次呼叫都是 400」。
    if getattr(engine.cfg, "preflight", False) and getattr(llm, "preflight", None):
        bad = await llm.preflight(engine.world)
        if bad:
            console.print("\n[bold red]✕ 開跑前檢查沒過，這些設定送不出去：[/bold red]")
            for model, thinking, err in bad:
                console.print(f"  [red]{model}　thinking={thinking}[/red]\n    {err[:300]}")
            console.print(
                "  [dim]先用 `truman.cli models` 對一次模型 ID 與價目；"
                "thinking_level 每個模型只吃一部分等級。[/dim]"
            )
            log.close()
            return 2
        if not quiet:
            console.print("[dim]開跑前檢查通過。[/dim]")
    try:
        for _ in range(ticks):
            if not quiet:
                console.rule(f"[dim]{clock_str(engine.world.tick)}  (tick {engine.world.tick})[/dim]")
            await engine.tick()
            if engine.aborted:
                break
        if not engine.aborted:
            await engine.finish()  # 收工補評一次覺察，見 Engine.finish
    finally:
        checkpoint.save(engine.world, engine.run_dir)
        stats = llm.stats()
        ok, bad = engine.ok_calls, engine.failed_calls
        log.write("run_summary", {
            "llm": stats, "awareness": engine.world.awareness_score,
            "ok_calls": ok, "failed_calls": bad, "last_error": engine.last_error,
        })
        log.close()
        _print_stats(stats)

    if bad:
        total = ok + bad
        console.print(
            f"\n[bold red]⚠ {bad}/{total} 次呼叫失敗[/bold red]"
            f"（成功 {ok}）。最後一個錯誤：\n  [red]{engine.last_error[:400]}[/red]\n"
            f"  完整紀錄：runs/{engine.world.run_id}/events.jsonl 裡的 think_failed。"
        )
    if engine.aborted:
        console.print(
            f"[bold red]這次 run 在第 {engine.world.tick} 刻中止了。[/bold red]"
            f"[red]{engine.abort_reason}[/red]"
        )
        return 1
    if ok == 0 and bad:
        console.print("[bold red]這次 run 沒有產生任何有效決策——世界狀態等同沒有推進。[/bold red]")
        return 1
    return 0


def _print_stats(stats: dict) -> None:
    t = Table(title=f"LLM 用量（{stats.get('_provider', '?')}）", show_edge=False)
    for col in ("層", "模型", "呼叫", "輸入", "輸出", "快取寫", "快取讀", "命中率", "成本 USD"):
        t.add_column(col, justify="right")
    for tier, s in stats.items():
        if tier.startswith("_"):
            continue
        t.add_row(
            tier, s["model"], str(s["calls"]), str(s["input_tokens"]),
            str(s["output_tokens"]), str(s["cache_write"]), str(s["cache_read"]),
            f"{s['cache_hit_rate']:.0%}", f"{s['cost_usd']:.4f}",
        )
    console.print(t)
    console.print(f"[bold]總成本 ≈ ${stats.get('_total_cost_usd', 0):.4f}[/bold]")


# ---------------------------------------------------------------- commands
def cmd_run(args) -> None:
    if not args.stub:
        require_credentials(args.provider)
    scen = load_scenario(args.scenario)
    cfg = build_config(args, use_cache=not args.no_cache,
                       combat=getattr(scen, "COMBAT", False))
    world = scen.build_world(args.run_id, args.seed)
    public_cast = (apply_cast(world, scen, args.cast, cfg.provider)
                   if getattr(args, "cast", None) else None)
    run_dir = RUNS / args.run_id
    engine, log, llm = make_engine(world, scen, cfg, run_dir, quiet=args.quiet, stub=args.stub,
                                   public_cast=public_cast)
    log.write("run_start", {"run_id": args.run_id, "scenario": scen.NAME, "seed": args.seed,
                            "ticks": args.ticks, "provider": cfg.provider, "cast": args.cast,
                            "models": cfg.models, "cfg": {"use_cache": cfg.use_cache}})
    sys.exit(asyncio.run(_drive(engine, log, llm, args.ticks, args.quiet)))


def cmd_replay(args) -> None:
    src = RUNS / args.run_id
    if not (src / "events.jsonl").exists():
        console.print(f"[red]找不到 {src/'events.jsonl'}[/red]")
        sys.exit(1)
    index = build_replay_index(src / "events.jsonl")
    console.print(f"[dim]載入 {len(index)} 筆 LLM 記錄，重放不會呼叫 API。[/dim]")

    scen = load_scenario(args.scenario)
    # combat 也要帶進來：少了它，replay 會把當初生效的 attack 全部駁回，
    # 整條時間線就對不上了。
    cfg = build_config(args, combat=getattr(scen, "COMBAT", False))
    world = scen.build_world(f"{args.run_id}_replay", args.seed)
    run_dir = RUNS / f"{args.run_id}_replay"
    engine, log, llm = make_engine(world, scen, cfg, run_dir, replay_index=index, quiet=args.quiet)
    sys.exit(asyncio.run(_drive(engine, log, llm, args.ticks, args.quiet)))


def cmd_fork(args) -> None:
    if not args.stub:
        require_credentials(args.provider)
    if args.from_latest:
        src = checkpoint.latest(RUNS / args.from_latest)
        if src is None:
            console.print(f"[red]{args.from_latest} 沒有 checkpoint[/red]")
            sys.exit(1)
    else:
        src = Path(args.from_checkpoint)

    scen = load_scenario(args.scenario)
    cfg = build_config(args, use_cache=not args.no_cache,
                       combat=getattr(scen, "COMBAT", False))
    world = checkpoint.fork(src, args.run_id)
    run_dir = RUNS / args.run_id
    engine, log, llm = make_engine(world, scen, cfg, run_dir, quiet=args.quiet, stub=args.stub)
    log.write("fork", {"from": str(src), "at_tick": world.tick, "run_id": args.run_id})

    for spec in args.inject or []:
        agent_id, _, text = spec.partition(":")
        engine.director.add_runtime(agent_id.strip(), text.strip(), world.tick)
        log.write("fork_injection", {"agent": agent_id.strip(), "text": text.strip()})

    console.print(f"[dim]從 {src.name} 分支，tick={world.tick}[/dim]")
    sys.exit(asyncio.run(_drive(engine, log, llm, args.ticks, args.quiet)))


def cmd_report(args) -> None:
    run_dir = RUNS / args.run_id
    events = list(EventLog.read(run_dir))
    if not events:
        console.print("[red]沒有事件[/red]")
        sys.exit(1)

    kinds: dict[str, int] = {}
    think_by_agent: dict[str, int] = {}
    coast = 0
    awareness: list[dict] = []
    reflections: list[dict] = []
    invalid: list[dict] = []
    failed: list[dict] = []
    speech: list[tuple[int, dict]] = []
    summary = None
    # 追蹤詞的預設值來自劇本，所以要知道這個 run 當初跑的是哪一本。
    scenario = getattr(args, "scenario", "seahaven")

    for ev in events:
        kinds[ev["type"]] = kinds.get(ev["type"], 0) + 1
        d = ev["data"]
        if ev["type"] == "think":
            think_by_agent[d["agent"]] = think_by_agent.get(d["agent"], 0) + 1
        elif ev["type"] == "coast":
            coast += 1
        elif ev["type"] == "awareness":
            awareness.append(d)
        elif ev["type"] == "reflection":
            reflections.append(d)
        elif ev["type"] == "invalid_intent":
            invalid.append(d)
        elif ev["type"] == "speech":
            speech.append((ev["tick"], d))
        elif ev["type"] == "run_summary":
            summary = d
        elif ev["type"] == "run_start":
            scenario = d.get("scenario", scenario)
        elif ev["type"] in ("think_failed", "reflect_failed", "judge_failed"):
            failed.append(d)

    total_decisions = sum(think_by_agent.values()) + coast
    console.rule(f"run: {args.run_id}")
    console.print(
        f"事件 {len(events)} 筆　|　決策點 {total_decisions}　"
        f"其中叫 LLM {sum(think_by_agent.values())}、節流跳過 {coast} "
        f"({coast/total_decisions:.0%})" if total_decisions else ""
    )

    if failed:
        # 失敗必須排在最前面。全滅的 run 也會有漂亮的成本表，那不代表它成功了。
        console.print(
            f"[bold red]⚠ {len(failed)} 次 LLM 呼叫失敗[/bold red]"
            f"（佔 {len(failed)/max(1, total_decisions):.0%} 的決策點）"
        )
        seen_err = set()
        for f in failed:
            e = (f.get("error") or "")[:220]
            if e not in seen_err:
                seen_err.add(e)
                console.print(f"  [red]{e}[/red]")

    t = Table(title="每人思考次數", show_edge=False)
    t.add_column("agent"); t.add_column("次數", justify="right")
    for k, v in sorted(think_by_agent.items(), key=lambda x: -x[1]):
        t.add_row(k, str(v))
    console.print(t)

    if awareness:
        console.rule("覺察軌跡")
        for a in awareness:
            if a["source"] == "llm_judge":
                console.print(f"[bold yellow]{a['when']}  評分 {a['score']}/10[/bold yellow] "
                              f"— {a['rationale']}")
                for e in a.get("evidence", [])[:3]:
                    console.print(f"    · {e}")
            else:
                console.print(f"[dim]{a['when']}  哨兵 +{a['delta']} "
                              f"({'、'.join(a['markers'])}) → {a['total']}[/dim]")

    if reflections:
        console.rule("reflection")
        for r in reflections:
            console.print(f"[cyan]{r['agent']}[/cyan]")
            for i in r["insights"]:
                console.print(f"    · {i}")

    if invalid:
        console.rule(f"被世界駁回的 intent（{len(invalid)} 次）")
        for i in invalid[:10]:
            console.print(f"[red]{i['agent']}[/red] {i['reason']}")

    fights = [(e["tick"], e["data"]) for e in events if e["type"] == "attack"]
    deaths = [(e["tick"], e["data"]) for e in events if e["type"] == "death"]
    if fights:
        console.rule(f"動手（{len(fights)} 次，死 {len(deaths)} 人）")
        for tick, d in fights:
            colour = "bold red" if d["target_wound"] >= 3 else "yellow"
            console.print(f"[{colour}]t{tick}  {d['line']}[/{colour}]")
        for tick, d in deaths:
            console.print(f"[bold red]  ☠ {d['when']}  {d['name']} 死於 {d['killed_by']} 之手[/bold red]")

    _report_goals(events)
    _report_arts(events)

    if speech:
        _report_social(speech, scenario, getattr(args, "track", None))
    _report_gatherings(events)

    if summary:
        console.rule("成本")
        _print_stats(summary["llm"])


def _report_goals(events: list[dict]) -> None:
    """誰做到了今天要做的事。

    這是箱庭最直接的成績單：同一張地圖、同一批目的，換一個人的腦袋或換一組絕技，
    達成率會不會變。沒有這張表就只能靠讀對話猜。
    """
    closed = [
        (e["tick"], e["type"], e["data"])
        for e in events
        if e["type"] in ("goal_done", "goal_failed")
    ]
    if not closed:
        return
    done = sum(1 for _, k, _ in closed if k == "goal_done")
    console.rule(f"目的（{done}/{len(closed)} 達成）")
    t = Table(show_edge=False)
    t.add_column("人"); t.add_column("要做到的事"); t.add_column("判定")
    t.add_column("結果", justify="center"); t.add_column("時刻", justify="right")
    for tick, kind, d in closed:
        ok = kind == "goal_done"
        t.add_row(
            d.get("name", d["agent"]),
            d.get("text", ""),
            d.get("kind", ""),
            f"[green]達成[/green]" if ok else "[red]沒做到[/red]",
            f"t{tick}",
        )
    console.print(t)
    for tick, kind, d in closed:
        if d.get("note"):
            colour = "green" if kind == "goal_done" else "red"
            console.print(f"  [{colour}]· {d.get('name')}：{d['note']}[/{colour}]")


def _report_arts(events: list[dict]) -> None:
    """絕技用了幾次、誰用的、有沒有用在刀口上。

    配了卻從來不用，和配了每次都用，是兩種不同的毛病：前者是說明寫得不夠清楚
    （或者這門功夫對他的目的根本沒用），後者是配額給太鬆。這張表就是要看出這件事。
    """
    used = [(e["tick"], e["data"]) for e in events if e["type"] == "art_used"]
    rejected = [
        e["data"] for e in events
        if e["type"] == "invalid_intent"
        and (e["data"].get("action") or {}).get("kind") == "use_art"
    ]
    if not used and not rejected:
        return
    console.rule(f"絕技（使出 {len(used)} 次，駁回 {len(rejected)} 次）")
    by: dict[tuple[str, str], int] = {}
    for _, d in used:
        by[(d.get("name", d["agent"]), d.get("art_name", d.get("art", "")))] = (
            by.get((d.get("name", d["agent"]), d.get("art_name", d.get("art", ""))), 0) + 1
        )
    if by:
        t = Table(show_edge=False)
        t.add_column("人"); t.add_column("絕技"); t.add_column("次數", justify="right")
        for (who, art), n in sorted(by.items(), key=lambda x: -x[1]):
            t.add_row(who, art, str(n))
        console.print(t)
    for tick, d in used[:20]:
        console.print(f"[magenta]t{tick}  {d.get('line', '')}[/magenta]")
    if rejected:
        # 駁回多半代表說明沒寫清楚——角色一直去用已經用盡或還在冷卻的功夫。
        console.print(f"[dim]駁回 {len(rejected)} 次，前幾個理由：[/dim]")
        seen = set()
        for r in rejected:
            why = r.get("reason", "")
            if why not in seen:
                seen.add(why)
                console.print(f"  [dim red]· {why}[/dim red]")
            if len(seen) >= 5:
                break


def _report_gatherings(events: list[dict], least: int = 3) -> None:
    """有沒有人自己聚起來——箱庭裡最值得看的一件事。

    位置從 arrive 事件重建。第一次 arrive 之前不知道人在哪，那段就不算，
    寧可漏報也不要編造。
    """
    where: dict[str, str] = {}
    seen: set[tuple[int, str, tuple[str, ...]]] = set()
    rows = []
    for ev in events:
        if ev["type"] != "arrive":
            continue
        where[ev["data"]["agent"]] = ev["data"]["area"]
        here = sorted(a for a, ar in where.items() if ar == ev["data"]["area"])
        if len(here) < least:
            continue
        key = (ev["data"]["area"], tuple(here))
        if key in seen:
            continue
        seen.add(key)  # type: ignore[arg-type]
        rows.append((ev["tick"], ev["data"]["area"], here))
    if not rows:
        return
    console.rule("聚在一起")
    t = Table(show_edge=False)
    for col in ("tick", "地點", "誰"):
        t.add_column(col)
    for tick, area, here in rows[:12]:
        t.add_row(f"t{tick}", area, "、".join(here))
    console.print(t)


def _report_social(speech: list[tuple[int, dict]], scenario: str, track: str | None) -> None:
    """箱庭觀測：誰跟誰講話、話題傳了多遠。

    這兩件事是「把人放進小鎮會發生什麼」唯一便宜又有訊息量的量法——
    都從既有日誌算得出來，不需要再叫一次 LLM。
    """
    names = {d["speaker"]: d["speaker_name"] for _, d in speech}
    pairs: dict[tuple[str, str], int] = {}
    said: dict[str, int] = {}
    for _, d in speech:
        who = d["speaker_name"]
        said[who] = said.get(who, 0) + 1
        if d.get("to"):
            key = (who, names.get(d["to"], d["to"]))
            pairs[key] = pairs.get(key, 0) + 1

    console.rule("誰跟誰說話")
    t = Table(show_edge=False)
    t.add_column("說話者"); t.add_column("對象"); t.add_column("次數", justify="right")
    for (a, b), n in sorted(pairs.items(), key=lambda x: -x[1])[:12]:
        t.add_row(a, b, str(n))
    console.print(t)
    console.print("[dim]發言數：" + "、".join(
        f"{k} {v}" for k, v in sorted(said.items(), key=lambda x: -x[1])) + "[/dim]")

    topics = [s.strip() for s in track.split(",") if s.strip()] if track else []
    if not topics:
        try:
            topics = list(getattr(load_scenario(scenario), "TRACK_TOPICS", []))
        except Exception:  # noqa: BLE001  劇本可能已改名或不存在，不該讓 report 掛掉
            topics = []
    if not topics:
        return

    console.rule("話題擴散")
    t = Table(show_edge=False)
    for col in ("詞", "首次", "誰先說的", "講過的人", "傳開耗時"):
        t.add_column(col)
    for word in topics:
        hits = [(tick, d) for tick, d in speech if word in d["utterance"]]
        if not hits:
            t.add_row(word, "—", "—", "0 人", "—")
            continue
        speakers: dict[str, int] = {}
        for tick, d in hits:
            speakers.setdefault(d["speaker_name"], tick)
        first_tick, first_d = hits[0]
        spread = max(speakers.values()) - first_tick if len(speakers) > 1 else None
        t.add_row(
            word,
            f"t{first_tick}",
            first_d["speaker_name"],
            f"{len(speakers)} 人",
            f"{spread} tick" if spread is not None else "沒傳開",
        )
    console.print(t)


def cmd_map(args) -> None:
    scen = load_scenario(args.scenario)
    grid = scen.build_grid()
    console.print(grid.brief())


def cmd_tokens(args) -> None:
    """量快取前綴的真實 token 數，判斷有沒有跨過最小可快取門檻。

    離線只能估（`truman.llm.tokens.estimate` 是刻意低估的下界）；
    有憑證時走 count_tokens 端點拿真值。絕不要用 tiktoken。
    """
    from .llm.prompts import persona_block
    from .llm.tokens import count_exact, estimate

    scen = load_scenario(args.scenario)
    cfg = build_config(args)
    grid = scen.build_grid()
    wb = scenario_world_block(scen, grid)
    world = scen.build_world("tokens", 0)

    exact = None
    try:
        exact = asyncio.run(count_exact(wb, cfg.models["dialogue"], cfg.provider))
    except Exception as e:  # noqa: BLE001 - 各家缺憑證時丟的例外型別不同
        hint = CRED_HINT.get(cfg.provider, "") if _looks_like_auth(e) else ""
        console.print(
            f"[dim]取不到 {cfg.provider} 的真值（{type(e).__name__}: {e}），"
            f"改用保守下界估算。{hint}[/dim]"
        )

    bp1 = exact or estimate(wb)
    # 斷點 2 涵蓋的是「世界＋人設」的累積前綴，不是人設單獨的大小。
    bp2 = {a.name: estimate(wb + persona_block(a)) for a in world.agents.values()}
    if exact:  # 有真值時，用同樣的比例把估算校正到真值刻度
        scale = exact / max(1, estimate(wb))
        bp2 = {k: int(v * scale) for k, v in bp2.items()}
    bp2_min = min(bp2.values())

    t = Table(title="快取前綴（累積）", show_edge=False)
    for col in ("層級", "內容", "字元", "tokens"):
        t.add_column(col, justify="right")
    t.add_row("共用", "世界（全 agent 相同）", str(len(wb)),
              f"{bp1} 真值" if exact else str(bp1))
    for a in world.agents.values():
        t.add_row("每人", f"世界＋{a.name}", str(len(wb) + len(persona_block(a))),
                  str(bp2[a.name]))
    console.print(t)

    note = (
        "斷點各自比對，不是只看斷點 1"
        if cfg.provider == "anthropic"
        else "Gemini 沒有顯式斷點：兩塊接成單一 system_instruction，由服務端找共同前綴"
    )
    console.print(f"\n[bold]門檻判定[/bold]（{note}）")
    for tier, model in cfg.models.items():
        floor = cfg.cache_min(model)
        m1 = "[green]會快取[/green]" if bp1 >= floor else "[red]靜默失效[/red]"
        m2 = "[green]會快取[/green]" if bp2_min >= floor else "[red]靜默失效[/red]"
        console.print(
            f"  {tier:<9} {model:<24} 門檻 {floor:>5}   "
            f"世界 {bp1:>5} {m1}   世界＋人設 {bp2_min:>5} {m2}"
        )
    console.print(
        "\n[dim]沒跨過門檻不會多收錢，只是沒有快取效益。估算是刻意壓低的下界，"
        "真值通常更高。最終請看 report 的『快取讀』欄位驗證真實命中。[/dim]"
    )


def cmd_models(args) -> None:
    """列出該 provider 目前實際可用的模型，並檢查設定裡的 ID 還在不在。

    模型會下架（Gemini 2.0 Flash 已於 2026-06-01 關閉），
    設定檔裡的 ID 過期就是 404，所以留這個出口對帳。
    """
    from .llm.tokens import list_models

    cfg = build_config(args)
    try:
        available = asyncio.run(list_models(cfg.provider))
    except Exception as e:  # noqa: BLE001
        # 不要一律當成憑證問題——把真正的例外印出來，否則會把 SDK 的錯誤誤導成缺 key。
        console.print(f"[red]查不到模型清單：{type(e).__name__}: {e}[/red]")
        if _looks_like_auth(e):
            console.print(f"  {CRED_HINT.get(cfg.provider, '')}")
        sys.exit(2)

    flat = {m.split("/")[-1] for m in available}
    console.rule(f"{cfg.provider}：設定中的模型")
    for tier, model in cfg.models.items():
        ok = model in flat
        mark = "[green]可用[/green]" if ok else "[red]查無此 ID[/red]"
        console.print(f"  {tier:<9} {model:<26} {mark}")
    console.rule(f"目前可用（{len(available)} 個）")
    for m in sorted(available):
        console.print(f"  {m}")


# ---------------------------------------------------------------- entry
def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(
        prog="truman",
        description="LLM 多智能體箱庭（現役主線：jianghu；seahaven 為早期楚門劇本）",
    )
    p.add_argument(
        "--scenario",
        default="jianghu",
        help="劇本（預設 jianghu；另有 tempest、hakoniwa、seahaven）",
    )
    p.add_argument(
        "--thinking",
        choices=["minimal", "low", "medium", "high"],
        help="Gemini 專屬：覆寫所有層的 thinking_level",
    )
    p.add_argument(
        "--model", action="append",
        help='覆寫模型：ID（全層）或 tier=ID（單層），可重複',
    )
    p.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=sorted(PROVIDERS),
        help=f"LLM 供應商（預設 {DEFAULT_PROVIDER}）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="跑一段新的模擬")
    r.add_argument("--run-id", default="demo")
    r.add_argument("--ticks", type=int, default=48)
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--no-cache", action="store_true")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--stub", action="store_true", help="用假 LLM 跑，不需憑證、不花錢")
    r.add_argument(
        "--cast",
        help="人物設定檔（JSON）：用它取代劇本裡的人。由 cast_editor.html 產生",
    )
    r.set_defaults(func=cmd_run)

    rp = sub.add_parser("replay", help="用既有日誌零成本重放")
    rp.add_argument("--run-id", required=True)
    rp.add_argument("--ticks", type=int, default=48)
    rp.add_argument("--seed", type=int, default=7)
    rp.add_argument("--quiet", action="store_true")
    rp.set_defaults(func=cmd_replay)

    f = sub.add_parser("fork", help="從 checkpoint 分支出反事實軌跡")
    f.add_argument("--from-latest", help="來源 run id（取最新 checkpoint）")
    f.add_argument("--from-checkpoint", help="checkpoint 檔路徑")
    f.add_argument("--run-id", required=True)
    f.add_argument("--ticks", type=int, default=24)
    f.add_argument("--inject", action="append", help='格式 "agent_id:要注入的觀察"')
    f.add_argument("--no-cache", action="store_true")
    f.add_argument("--quiet", action="store_true")
    f.add_argument("--stub", action="store_true", help="用假 LLM 跑，不需憑證、不花錢")
    f.set_defaults(func=cmd_fork)

    rep = sub.add_parser("report", help="彙整一次 run")
    rep.add_argument("--run-id", required=True)
    rep.add_argument(
        "--track",
        help="話題擴散要追的詞，逗號分隔。留空則用劇本的 TRACK_TOPICS",
    )
    rep.set_defaults(func=cmd_report)

    m = sub.add_parser("map", help="印出地圖")
    m.set_defaults(func=cmd_map)

    tk = sub.add_parser("tokens", help="量快取前綴大小 vs 最小可快取門檻")
    tk.set_defaults(func=cmd_tokens)

    md = sub.add_parser("models", help="列出可用模型，對帳設定裡的 ID 有沒有過期")
    md.set_defaults(func=cmd_models)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
