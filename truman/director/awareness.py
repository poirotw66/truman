"""覺察偵測 —— 主角有沒有察覺世界是假的？

兩層：
  1. 樣式哨兵（免費，每 tick 跑）：關鍵詞比對，抓明顯訊號。
  2. LLM 評審（每 N tick 跑一次）：讀主角最近的內心話，給 0–10 分。

刻意**不**在主角的 action schema 裡放 suspicion 欄位——那等於每一 tick 都在
提示他「你應該懷疑」，會直接汙染要測的東西。偵測必須是外部的、事後的。
"""

from __future__ import annotations

from ..config import clock_str
from ..llm.client import Call
from ..llm.prompts import AWARENESS_SYSTEM
from ..llm.schemas import AWARENESS_SCHEMA


def pattern_scan(text: str, markers: list[str]) -> list[str]:
    return [m for m in markers if m in (text or "")]


def score_tick(world, agent, thought: str, utterance: str, cfg, log) -> float:
    """每 tick 的廉價哨兵。回傳這一 tick 增加的分數。"""
    hits = pattern_scan(thought, cfg.suspicion_markers)
    hits += pattern_scan(utterance, cfg.suspicion_markers)
    if not hits:
        return 0.0
    delta = 0.5 * len(set(hits))
    world.awareness_score += delta
    entry = {
        "tick": world.tick,
        "when": clock_str(world.tick),
        "source": "pattern",
        "markers": sorted(set(hits)),
        "delta": delta,
        "total": round(world.awareness_score, 2),
        "thought": thought,
    }
    world.awareness_log.append(entry)
    log.write("awareness", entry)
    return delta


def judge_call(world, agent, cfg) -> Call | None:
    """組出 LLM 評審請求。沒有足夠素材就回 None。"""
    thoughts = [m for m in agent.memory.entries if m.kind == "thought"][-30:]
    if len(thoughts) < 3:
        return None
    body = "\n".join(f"[{m.when}] {m.content}" for m in thoughts)
    return Call(
        key=f"{world.tick}:{agent.id}:awareness",
        tier="judge",
        system_blocks=[AWARENESS_SYSTEM],
        user_message=f"以下是這個人最近的內心獨白與發言：\n\n{body}",
        schema=AWARENESS_SCHEMA,
        max_tokens=700,
    )


def apply_judgement(world, result: dict, log) -> None:
    entry = {
        "tick": world.tick,
        "when": clock_str(world.tick),
        "source": "llm_judge",
        "score": result.get("score", 0),
        "evidence": result.get("evidence", []),
        "rationale": result.get("rationale", ""),
    }
    world.awareness_log.append(entry)
    log.write("awareness", entry)
