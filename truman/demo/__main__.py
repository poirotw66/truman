"""python -m truman.demo → 開本機 demo 入口。"""

from __future__ import annotations

import argparse
import webbrowser

from dotenv import load_dotenv

from truman.demo.server import serve


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Truman demo 前端入口（回放 + 現場開跑）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="不要自動開瀏覽器")
    args = ap.parse_args(argv)

    httpd = serve(args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"Truman demo → {url}", flush=True)
    print("  箱庭入口：回放既有軌跡，或現場開一場（預設真 LLM · 96 ticks）。Ctrl+C 結束。", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
