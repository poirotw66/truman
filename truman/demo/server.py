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


def list_runs() -> list[dict]:
    out: list[dict] = []
    if not RUNS.exists():
        return out
    for d in sorted(RUNS.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        ev = d / "events.jsonl"
        if not ev.exists():
            continue
        max_tick = -1
        n = 0
        with ev.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    tick = json.loads(line).get("tick")
                except json.JSONDecodeError:
                    continue
                if isinstance(tick, int) and tick > max_tick:
                    max_tick = tick
        cps = list((d / "checkpoints").glob("t*.json")) if (d / "checkpoints").is_dir() else []
        out.append(
            {
                "id": d.name,
                "events": n,
                "max_tick": max_tick,
                "when": clock_str(max_tick) if max_tick >= 0 else "",
                "checkpoints": len(cps),
            }
        )
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
