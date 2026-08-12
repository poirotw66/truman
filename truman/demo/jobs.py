"""Demo 現場開跑：單鎖背景 job，重用 CLI 的 engine 組裝。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from truman import cli as truman_cli
from truman.config import clock_str
from truman.obs import checkpoint

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
DEMO_OUT = RUNS / "_demo"


@dataclass
class Job:
    id: str
    run_id: str
    status: str = "queued"  # queued | running | done | error
    tick: int = 0
    ticks_total: int = 0
    when: str = ""
    phase: str = "queued"
    error: str = ""
    replay_url: str = ""
    recent: list[dict] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    updated_at: float = 0.0


class JobRunner:
    """本機同時只允許一個 live job。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: Job | None = None
        self._thread: threading.Thread | None = None

    @property
    def current(self) -> Job | None:
        return self._job

    def get(self, job_id: str) -> Job | None:
        if self._job and self._job.id == job_id:
            return self._job
        return None

    def start(self, params: dict) -> Job:
        with self._lock:
            if self._job and self._job.status in ("queued", "running"):
                raise RuntimeError("已有一場模擬在跑，請等它結束。")
            job = Job(
                id=uuid.uuid4().hex[:10],
                run_id=params["run_id"],
                ticks_total=int(params["ticks"]),
                started_at=time.time(),
                updated_at=time.time(),
            )
            self._job = job
            self._thread = threading.Thread(
                target=self._run_thread, args=(job, params), daemon=True
            )
            self._thread.start()
            return job

    def _run_thread(self, job: Job, params: dict) -> None:
        job.status = "running"
        job.phase = "啟動中"
        job.updated_at = time.time()
        try:
            code = asyncio.run(self._drive(job, params))
            if code != 0 and not job.error:
                job.error = "模擬結束但沒有有效決策（可能全部呼叫失敗）。"
                job.status = "error"
                job.phase = "失敗"
                job.finished_at = time.time()
                job.updated_at = job.finished_at
                return
            if (params.get("scenario") or "jianghu") == "jianghu":
                slug = self._build_replay(job.run_id)
                job.replay_url = f"/replay/{slug}"
            job.status = "done"
            job.phase = "已完成"
            job.finished_at = time.time()
            job.updated_at = job.finished_at
        except Exception as e:  # noqa: BLE001 - 邊界：回報給 UI
            job.error = f"{type(e).__name__}: {e}"
            job.status = "error"
            job.phase = "失敗"
            job.finished_at = time.time()
            job.updated_at = job.finished_at

    async def _drive(self, job: Job, params: dict) -> int:
        stub = bool(params.get("stub", False))
        provider = params.get("provider") or "gemini"
        scenario = params.get("scenario") or "jianghu"
        seed = int(params.get("seed", 7))
        ticks = int(params["ticks"])
        cast = params.get("cast") or None
        run_id = params["run_id"]

        if not stub:
            # CLI 的 require_credentials 會 sys.exit；這裡改成丟例外給 UI。
            try:
                truman_cli.require_credentials(provider)
            except SystemExit as e:
                raise RuntimeError(
                    f"找不到 {provider} 的憑證。請設 API key，或改用 stub。"
                ) from e

        run_dir = RUNS / run_id
        if (run_dir / "events.jsonl").exists():
            raise RuntimeError(f"runs/{run_id} 已存在，請換一個 run_id。")

        args = SimpleNamespace(
            provider=provider,
            thinking=None,
            model=None,
            no_cache=False,
            quiet=True,
            stub=stub,
            cast=cast,
            run_id=run_id,
            ticks=ticks,
            seed=seed,
            scenario=scenario,
        )
        scen = truman_cli.load_scenario(scenario)
        cfg = truman_cli.build_config(
            args, use_cache=True, combat=getattr(scen, "COMBAT", False)
        )
        world = scen.build_world(run_id, seed)
        public_cast = None
        if cast:
            try:
                public_cast = truman_cli.apply_cast(world, scen, cast, cfg.provider)
            except SystemExit as e:
                raise RuntimeError(f"人物設定檔無法套用：{e}") from e

        engine, log, llm = truman_cli.make_engine(
            world, scen, cfg, run_dir, quiet=True, stub=stub, public_cast=public_cast
        )
        log.write(
            "run_start",
            {
                "run_id": run_id,
                "scenario": scen.NAME,
                "seed": seed,
                "ticks": ticks,
                "provider": cfg.provider,
                "cast": cast,
                "models": cfg.models,
                "cfg": {"use_cache": cfg.use_cache},
                "via": "demo",
            },
        )

        ok = bad = 0
        # 開跑前先驗設定。現場 demo 更需要這一步：頁面上只看得到進度在跑，
        # 看不出每一次呼叫其實都在 400（j3 就是這樣「跑完」96 拍的）。
        if getattr(cfg, "preflight", False) and getattr(llm, "preflight", None):
            job.phase = "開跑前檢查模型設定"
            job.updated_at = time.time()
            trouble = await llm.preflight(world)
            if trouble:
                model, thinking, err = trouble[0]
                job.error = (f"開跑前檢查沒過：{model}（thinking={thinking}）送不出去。"
                             f"{err[:200]}")
                log.write("preflight_failed", {"failed": [list(x) for x in trouble]})
                log.close()
                return 2

        try:
            for _ in range(ticks):
                job.tick = world.tick
                job.when = clock_str(world.tick)
                job.phase = f"正在跑第 {world.tick}/{ticks} 刻"
                job.updated_at = time.time()
                await engine.tick()
                self._pull_recent(job, run_dir)
                if engine.aborted:
                    break
            if not engine.aborted:
                await engine.finish()
        finally:
            checkpoint.save(engine.world, engine.run_dir)
            stats = llm.stats()
            ok, bad = engine.ok_calls, engine.failed_calls
            log.write(
                "run_summary",
                {
                    "llm": stats,
                    "awareness": engine.world.awareness_score,
                    "ok_calls": ok,
                    "failed_calls": bad,
                    "last_error": engine.last_error,
                },
            )
            log.close()
            job.tick = world.tick
            job.when = clock_str(max(0, world.tick - 1))
            job.updated_at = time.time()
        if engine.aborted:
            job.error = engine.abort_reason or "全部呼叫失敗，已中止"
            return 1
        if ok == 0 and bad:
            job.error = engine.last_error or "全部呼叫失敗"
            return 1
        return 0

    def _pull_recent(self, job: Job, run_dir: Path, limit: int = 12) -> None:
        path = run_dir / "events.jsonl"
        if not path.exists():
            return
        wanted = ("speech", "attack", "death", "director", "tick_start")
        recent: list[dict] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("type") not in wanted:
                    continue
                recent.append(self._summarize(rec))
        job.recent = recent[-limit:]
        job.updated_at = time.time()

    @staticmethod
    def _summarize(rec: dict) -> dict:
        ty, d, tick = rec["type"], rec.get("data") or {}, rec.get("tick")
        if ty == "speech":
            text = f"{d.get('speaker_name', '?')}：「{d.get('utterance', '')}」"
        elif ty == "attack":
            text = d.get("line") or f"{d.get('attacker')} 出手"
        elif ty == "death":
            text = f"{d.get('name')} 倒下（{d.get('killed_by', '')}）"
        elif ty == "director":
            text = d.get("text") or "導演事件"
        elif ty == "tick_start":
            text = d.get("when") or f"tick {tick}"
        else:
            text = ty
        return {"type": ty, "tick": tick, "text": text}

    def _build_replay(self, run_id: str) -> str:
        import sys

        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from replay.build_frames import build_replay

        DEMO_OUT.mkdir(parents=True, exist_ok=True)
        slug = run_id
        out = DEMO_OUT / f"{slug}_replay.html"
        build_replay([run_id], out, write_frames_json=False, quiet=True)
        return slug


RUNNER = JobRunner()
