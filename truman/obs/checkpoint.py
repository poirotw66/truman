"""Checkpoint 與世界分支。

分支（counterfactual fork）是這個模擬唯一能做因果推論的手段：
從 tick t 分岔成兩支，一支注入事件、一支不注入，其餘完全相同，
比較兩支的差異才能說「是這個事件造成的」。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..world.state import WorldState


def checkpoint_dir(run_dir: Path) -> Path:
    d = Path(run_dir) / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(world: WorldState, run_dir: Path) -> Path:
    path = checkpoint_dir(run_dir) / f"t{world.tick:05d}.json"
    path.write_text(
        json.dumps(world.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load(path: Path) -> WorldState:
    return WorldState.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def latest(run_dir: Path) -> Path | None:
    d = Path(run_dir) / "checkpoints"
    if not d.exists():
        return None
    files = sorted(d.glob("t*.json"))
    return files[-1] if files else None


def fork(src_checkpoint: Path, new_run_id: str) -> WorldState:
    """從 checkpoint 分岔出一條新軌跡。世界狀態完全相同，只換 run_id。"""
    world = load(src_checkpoint)
    world.run_id = new_run_id
    return world
