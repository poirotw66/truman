"""全量事件日誌（JSONL）。

這是整個專案的命脈：所有 LLM 的 input/output、所有 intent、所有世界事件都留檔。
沒有它就不能 replay，不能 replay 就等於每次分析都要重新燒錢，
而且出了有趣現象也重跑不出來。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class EventLog:
    def __init__(self, run_dir: Path, tick_ref=None):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "events.jsonl"
        self._fh = self.path.open("a", encoding="utf-8")
        self._seq = 0
        self._tick_ref = tick_ref  # 可呼叫物件，回傳目前 tick

    def bind_tick(self, fn) -> None:
        self._tick_ref = fn

    def write(self, kind: str, data: dict) -> None:
        self._seq += 1
        rec = {
            "seq": self._seq,
            "tick": self._tick_ref() if self._tick_ref else None,
            "wall": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "type": kind,
            "data": data,
        }
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------ 讀取
    @staticmethod
    def read(run_dir: Path):
        path = Path(run_dir) / "events.jsonl"
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
