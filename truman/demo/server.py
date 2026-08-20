"""本機 demo HTTP server：回放既有 run、現場開跑、SSE 進度。"""

from __future__ import annotations

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from truman.config import clock_str
from truman.demo.jobs import DEMO_OUT, ROOT, RUNS, RUNNER

STATIC = Path(__file__).with_name("static")
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _ensure_root_on_path() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


# 「敘事事件數」只算台詞、動手、死亡、目的結案、絕技、暴潮結算這些會真的出現在回放事件流
# 裡的事件；不算 llm_call／tick_start／snapshot／think 這類每拍都會有一堆的內部紀錄——不然
# 事件總數會被這些雜訊灌水，跟「這場有沒有戲」脫鉤（j3 全滅仍有 1359 筆事件就是這樣灌出來的）。
_NARRATIVE_TYPES = {"speech", "attack", "art_used", "death", "goal_done", "goal_failed", "storm"}
_FORK_FROM_RE = re.compile(r"[\\/]([^\\/]+)[\\/]checkpoints[\\/]")


def _run_scenario_fallback(run_dir: Path) -> str | None:
    """fork 出來的續集（例如 j2b、j2c）沒有自己的 run_start，退回讀第一個 checkpoint 的
    scenario 欄位——跟 replay/build_frames.py 的 detect_scenario() 用同一套退路。"""
    cps = sorted((run_dir / "checkpoints").glob("t*.json")) if (run_dir / "checkpoints").is_dir() else []
    if not cps:
        return None
    try:
        return json.loads(cps[0].read_text(encoding="utf-8")).get("scenario")
    except (OSError, json.JSONDecodeError):
        return None


def _scan_run(d: Path) -> dict:
    """掃一遍 events.jsonl，抓出排序與「全滅」標示要用的訊號。"""
    max_tick = -1
    n = 0
    eff = 0
    scenario: str | None = None
    ok_calls = failed_calls = None
    fork_at: int | None = None
    fork_parent: str | None = None
    with (d / "events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ty = r.get("type")
            data = r.get("data") or {}
            tick = r.get("tick")
            if isinstance(tick, int) and tick > max_tick:
                max_tick = tick
            if ty in _NARRATIVE_TYPES:
                eff += 1
            elif ty == "director" and data.get("fired") and data.get("text"):
                eff += 1
            elif ty == "run_start":
                scenario = data.get("scenario") or scenario
            elif ty == "run_summary":
                ok_calls = data.get("ok_calls")
                failed_calls = data.get("failed_calls")
            elif ty == "fork":
                fork_at = data.get("at_tick")
                m = _FORK_FROM_RE.search(str(data.get("from") or ""))
                fork_parent = m.group(1) if m else None
    if scenario is None:
        scenario = _run_scenario_fallback(d) or "jianghu"
    cps = list((d / "checkpoints").glob("t*.json")) if (d / "checkpoints").is_dir() else []
    return {
        "id": d.name,
        "events": n,
        "max_tick": max_tick,
        "when": clock_str(max_tick) if max_tick >= 0 else "",
        "checkpoints": len(cps),
        "scenario": scenario,
        "eff_events": eff,
        "ok_calls": ok_calls,
        "failed_calls": failed_calls,
        "failed": ok_calls == 0 and bool(failed_calls),
        "fork_at": fork_at,
        "fork_parent": fork_parent,
    }


def list_runs() -> list[dict]:
    """回放清單：不照字母排序——照「有沒有效」與「有多少份量」排。

    - 每個 run 的最後一筆事件是 run_summary，裡面有 ok_calls／failed_calls。
      ok_calls==0 且 failed_calls>0 就是全部 LLM 呼叫都失敗、世界原地沒動的「全滅」run
      （例如 j3：576 次呼叫全失敗，thinking_level 設定錯）。這種 run 標成 failed=true、
      永遠排到清單最後——它是有價值的失敗紀錄，不隱藏，但不能讓人以為是最完整的一場而誤點。
    - fork 出來的續集（例如 j2 → t24 fork → j2b → t70 fork → j2c）算成同一條故事線：
      排序看的是整條線最終跑到第幾拍、整條線總共有多少敘事事件，而不是只看單一檔案自己的
      長度——不然故事的開頭那段會因為「自己」只有 20-30 拍就被誤判成測試殘骸，排到後面去。
      鏈裡的每個檔案還是各自一個可選的 run，只是排序時綁在一起、依接手的那一拍由早到晚排列，
      讀起來像接續的故事（j2 在前，j2b 接著，j2c 收尾）。
    - 沒有續集的 run（多數的 0728／ab25／demo_api_*／g1／g2／x 這類只有 0–3 拍的測試殘骸）
      就照自己的 tick 數與敘事事件數排，兩者都低的自然沉到後面去。
    """
    if not RUNS.exists():
        return []
    scanned: dict[str, dict] = {}
    for d in sorted(RUNS.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or not (d / "events.jsonl").exists():
            continue
        scanned[d.name] = _scan_run(d)

    children: dict[str, list[str]] = {}
    for rid, info in scanned.items():
        parent = info["fork_parent"]
        if parent and parent in scanned:
            children.setdefault(parent, []).append(rid)
        else:
            info["fork_parent"] = None  # parent 不在這批 runs 裡（被刪了之類），就當沒有

    def chain_members(root: str) -> list[str]:
        out, stack = [], [root]
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(children.get(cur, []))
        return out

    chain_reach: dict[str, int] = {}
    chain_weight: dict[str, int] = {}
    chain_root: dict[str, str] = {}
    for root in (rid for rid, info in scanned.items() if not info["fork_parent"]):
        members = chain_members(root)
        reach = max(scanned[m]["max_tick"] for m in members)
        weight = sum(scanned[m]["eff_events"] for m in members)
        for m in members:
            chain_reach[m] = reach
            chain_weight[m] = weight
            chain_root[m] = root

    def sort_key(rid: str):
        info = scanned[rid]
        start = info["fork_at"] if info["fork_at"] is not None else 0
        # False < True，所以全滅的 run 自然排到最後；其餘依鏈的份量（reach、weight）由大到小，
        # 同一條鏈用 chain_root 綁在一起，鏈內再依自己接手的那一拍由早到晚排。
        return (info["failed"], -chain_reach[rid], -chain_weight[rid], chain_root[rid], start)

    out = []
    for rid in sorted(scanned, key=sort_key):
        info = scanned[rid]
        out.append({
            "id": rid,
            "events": info["events"],
            "max_tick": info["max_tick"],
            "when": info["when"],
            "checkpoints": info["checkpoints"],
            "scenario": info["scenario"],
            "failed": info["failed"],
            "ok_calls": info["ok_calls"],
            "failed_calls": info["failed_calls"],
        })
    return out


def build_replay_html(runs: list[str]) -> tuple[str, str]:
    """回傳 (slug, url_path)。"""
    _ensure_root_on_path()
    from replay.build_frames import build_replay

    if not runs:
        raise ValueError("請至少選一個 run")
    for r in runs:
        if not SLUG_RE.match(r):
            raise ValueError(f"非法 run id：{r}")
        if not (RUNS / r / "events.jsonl").exists():
            raise FileNotFoundError(f"找不到 runs/{r}/events.jsonl")

    DEMO_OUT.mkdir(parents=True, exist_ok=True)
    slug = "+".join(runs)
    # 檔名不能有 + 在某些環境怪，用 __
    file_slug = "__".join(runs)
    out = DEMO_OUT / f"{file_slug}_replay.html"
    build_replay(runs, out, write_frames_json=False, quiet=True)
    return file_slug, f"/replay/{file_slug}"


def job_public(job) -> dict:
    return {
        "job_id": job.id,
        "run_id": job.run_id,
        "status": job.status,
        "tick": job.tick,
        "ticks_total": job.ticks_total,
        "when": job.when,
        "phase": getattr(job, "phase", ""),
        "error": job.error,
        "replay_url": job.replay_url,
        "recent": job.recent,
        "board": getattr(job, "board", []),
        "outcome": getattr(job, "outcome", ""),
        "outcome_text": getattr(job, "outcome_text", ""),
        "started_at": getattr(job, "started_at", 0.0),
        "updated_at": getattr(job, "updated_at", 0.0),
        "server_now": time.time(),
    }


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "TrumanDemo/0.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict | list) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_file(STATIC / "index.html", "text/html; charset=utf-8")
            return
        if path == "/api/runs":
            self._json(200, {"runs": list_runs()})
            return
        if path.startswith("/api/job/") and path.endswith("/events"):
            job_id = path[len("/api/job/") : -len("/events")]
            self._sse(job_id)
            return
        if path.startswith("/api/job/"):
            job_id = path[len("/api/job/") :]
            job = RUNNER.get(job_id)
            if not job:
                self._json(404, {"error": "找不到這個 job"})
                return
            self._json(200, job_public(job))
            return
        if path.startswith("/replay/"):
            slug = unquote(path[len("/replay/") :])
            if not re.match(r"^[A-Za-z0-9_-]+(__[A-Za-z0-9_-]+)*$", slug):
                self._json(400, {"error": "非法 slug"})
                return
            html = DEMO_OUT / f"{slug}_replay.html"
            if not html.exists():
                self._json(404, {"error": f"還沒有回放檔 {slug}"})
                return
            self._serve_file(html, "text/html; charset=utf-8")
            return
        # static assets next to index if any
        rel = path.lstrip("/")
        candidate = (STATIC / rel).resolve()
        if candidate.is_file() and str(candidate).startswith(str(STATIC.resolve())):
            ctype = {
                ".css": "text/css",
                ".js": "application/javascript",
                ".html": "text/html; charset=utf-8",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".svg": "image/svg+xml",
            }.get(candidate.suffix.lower(), "application/octet-stream")
            self._serve_file(candidate, ctype)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._json(400, {"error": "JSON 解析失敗"})
            return

        if path == "/api/replay":
            runs = body.get("runs") or []
            if isinstance(runs, str):
                runs = [runs]
            try:
                slug, url = build_replay_html(list(runs))
            except (ValueError, FileNotFoundError, RuntimeError) as e:
                self._json(400, {"error": str(e)})
                return
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": f"{type(e).__name__}: {e}"})
                return
            self._json(200, {"slug": slug, "url": url})
            return

        if path == "/api/run":
            run_id = (body.get("run_id") or "").strip()
            if not run_id or not SLUG_RE.match(run_id):
                self._json(400, {"error": "run_id 只能用字母數字底線"})
                return
            try:
                ticks = int(body.get("ticks", 24))
            except (TypeError, ValueError):
                self._json(400, {"error": "ticks 必須是數字"})
                return
            if ticks < 1 or ticks > 96:
                self._json(400, {"error": "ticks 請介於 1–96（demo 上限一天）"})
                return
            params = {
                "run_id": run_id,
                "ticks": ticks,
                "seed": int(body.get("seed", 7)),
                "scenario": (body.get("scenario") or "jianghu").strip(),
                "provider": (body.get("provider") or "gemini").strip(),
                "stub": bool(body.get("stub", False)),
                "cast": (body.get("cast") or "").strip() or None,
            }
            try:
                job = RUNNER.start(params)
            except RuntimeError as e:
                self._json(409, {"error": str(e)})
                return
            self._json(200, job_public(job))
            return

        self._json(404, {"error": "not found"})

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._json(404, {"error": "檔案不存在"})
            return
        data = path.read_bytes()
        self._send(200, data, content_type)

    def _sse(self, job_id: str) -> None:
        job = RUNNER.get(job_id)
        if not job:
            self._json(404, {"error": "找不到這個 job"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_payload = None
        last_push = 0.0
        try:
            while True:
                job = RUNNER.get(job_id)
                if not job:
                    self._sse_event({"status": "gone"})
                    break
                payload = job_public(job)
                now = time.time()
                if payload != last_payload or now - last_push >= 1.0:
                    self._sse_event(payload)
                    last_payload = payload
                    last_push = now
                if job.status in ("done", "error"):
                    break
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _sse_event(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


def serve(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    DEMO_OUT.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), DemoHandler)
    return httpd
