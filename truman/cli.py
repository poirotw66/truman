"""CLI：跑模擬、重放、分支、出報告。

  python -m truman.cli run     --ticks 48 --run-id demo
  python -m truman.cli replay  --run-id demo               # 零成本重放
  python -m truman.cli fork    --from-latest demo --run-id demo_b --ticks 24 \\
                               --inject "chen_yuan:（你在抽屜深處找到一張沒見過的船票。）"
  python -m truman.cli report  --run-id demo
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

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
console = Console()


def load_scenario(name: str):
    return importlib.import_module(f"scenarios.{name}")


def build_config(args, **kw) -> SimConfig:
    """套用 --model 覆寫。接受 `ID`（全層）或 `tier=ID`（單層），可重複。"""
    cfg = SimConfig(provider=args.provider, **kw)
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


def make_engine(world, scen, cfg, run_dir: Path, replay_index=None, quiet=False, stub=False):
    grid = scen.build_grid()
    log = EventLog(run_dir)
    log.bind_tick(lambda: world.tick)
    if stub:
        from .llm.stub import StubLLM

        llm = StubLLM(cfg=cfg, log=log)
    else:
        llm = make_client(cfg=cfg, log=log, replay=replay_index)
    director = Director(script=list(scen.DIRECTOR_SCRIPT), log=log)
    engine = Engine(
        world=world,
        grid=grid,
        cfg=cfg,
        llm=llm,
        director=director,
        log=log,
        world_block_text=world_block(
            grid, scen.BRIEF, scen.NORMS, getattr(scen, "PUBLIC_CAST", "")
        ),
        run_dir=run_dir,
        console=None if quiet else console,
    )
    return engine, log, llm


async def _drive(engine, log, llm, ticks: int, quiet: bool) -> int:
    """回傳結束碼。全部呼叫都失敗時回 1——這種 run 不該看起來像成功。"""
    try:
        for _ in range(ticks):
            if not quiet:
                console.rule(f"[dim]{clock_str(engine.world.tick)}  (tick {engine.world.tick})[/dim]")
            await engine.tick()
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
    cfg = build_config(args, use_cache=not args.no_cache)
    world = scen.build_world(args.run_id, args.seed)
    run_dir = RUNS / args.run_id
    engine, log, llm = make_engine(world, scen, cfg, run_dir, quiet=args.quiet, stub=args.stub)
    log.write("run_start", {"run_id": args.run_id, "scenario": scen.NAME, "seed": args.seed,
                            "ticks": args.ticks, "provider": cfg.provider,
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
    cfg = build_config(args)
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
    cfg = build_config(args, use_cache=not args.no_cache)
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
    summary = None

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
        elif ev["type"] == "run_summary":
            summary = d
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

    if summary:
        console.rule("成本")
        _print_stats(summary["llm"])


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
    wb = world_block(grid, scen.BRIEF, scen.NORMS, getattr(scen, "PUBLIC_CAST", ""))
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
    p = argparse.ArgumentParser(prog="truman", description="單主角楚門式 LLM 社會模擬")
    p.add_argument("--scenario", default="seahaven")
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
