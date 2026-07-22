"""認知層：什麼時候該叫 LLM、用哪一層模型、prompt 怎麼組、記憶怎麼寫。

節流閥（`needs_llm`）是成本的最大槓桿。多數 tick 裡沒事發生——
agent 只是在往目的地走。那些 tick 一次 LLM 都不該叫。
"""

from __future__ import annotations

from ..config import clock_str
from ..llm.client import Call
from ..llm.prompts import (
    observation_message,
    persona_block,
    reflection_message,
)
from ..llm.schemas import ACTION_SCHEMA, REFLECTION_SCHEMA

# ---------------------------------------------------------------- 顯著度門檻

TIER_ORDER = ["routine", "dialogue", "reflect"]


def needs_llm(agent, obs, cfg, tick: int) -> tuple[bool, str]:
    """事件驅動而非固定 tick —— 沒事發生就不叫 LLM。"""
    if agent.action is None:
        return True, "no_action"
    if agent.action.get("done"):
        return True, "action_done"
    if obs.addressed_to_me():
        return True, "spoken_to"
    if obs.injected:
        return True, "director_event"
    if obs.new_faces(agent.seen_last_tick):
        return True, "new_face"
    if obs.heard:
        return True, "overheard"
    if tick - agent.last_think_tick >= cfg.forced_think_interval:
        return True, "periodic"
    return False, "coasting"


def pick_tier(agent, reason: str, obs, cfg) -> str:
    """主角是被觀測對象，保真度優先，永遠不走最便宜那層。"""
    social = reason in ("spoken_to", "overheard", "new_face", "director_event")
    tier = "dialogue" if social else "routine"
    if agent.is_protagonist:
        floor = cfg.protagonist_min_tier
        if TIER_ORDER.index(tier) < TIER_ORDER.index(floor):
            tier = floor
    return tier


# ---------------------------------------------------------------- 請求組裝


def action_call(agent, obs, world_block_text: str, cfg, tier: str, tick: int) -> Call:
    memories = agent.memory.retrieve(obs.retrieval_query(), tick, cfg.retrieval_k, cfg)
    return Call(
        key=f"{tick}:{agent.id}:act",
        tier=tier,
        # 順序即快取層級：世界（全共用）→ 人設＋信念（每人一份，只在 reflection 變）
        system_blocks=[world_block_text, persona_block(agent)],
        user_message=observation_message(obs, memories),
        schema=ACTION_SCHEMA,
        max_tokens=cfg.max_output_tokens,
    )


def reflection_call(agent, world_block_text: str, cfg, tick: int) -> Call:
    memories = agent.memory.recent(cfg.reflection_window)
    return Call(
        key=f"{tick}:{agent.id}:reflect",
        tier="reflect",
        system_blocks=[world_block_text, persona_block(agent)],
        user_message=reflection_message(agent, memories, clock_str(tick)),
        schema=REFLECTION_SCHEMA,
        # reflect 層跑高推理強度，thinking token 也吃這個額度。
        # 實測 2000 會把 insights 陣列切在字串中間（JSON 直接壞掉），拉到 4000。
        max_tokens=4000,
    )


def should_reflect(agent, cfg) -> bool:
    return agent.memory.importance_since_reflection >= cfg.reflection_threshold


def apply_reflection(agent, result: dict, cfg, tick: int) -> list[str]:
    when = clock_str(tick)
    insights = [s for s in result.get("insights", []) if s.strip()]
    for s in insights:
        agent.memory.add(tick, when, "reflection", s, importance=8)
    beliefs = [b for b in result.get("beliefs", []) if b.strip()]
    if beliefs:
        # 注意：改動 beliefs 會讓 system[1] 的位元組改變，付一次 cache write。
        # 這是刻意的——reflection 本來就該罕見。
        agent.memory.set_beliefs(beliefs, cfg.max_beliefs)
    agent.memory.importance_since_reflection = 0
    agent.memory.last_reflection_tick = tick
    return insights


# ---------------------------------------------------------------- 記憶寫入
# importance 目前用規則給分。升級路徑是換成一次 LLM 評分呼叫，
# 但那會讓每 tick 的呼叫數翻倍，Phase 1 不值得。

IMPORTANCE = {
    "spoken_to": 7,
    "overheard": 4,
    "new_face": 3,
    "arrival": 2,
    "thought": 4,
    "own_speech": 5,
    "injected": 7,
}


def record_perception(agent, obs, cfg, tick: int) -> None:
    when = clock_str(tick)

    for h in obs.heard:
        directed = h.get("to") == agent.id
        agent.memory.add(
            tick,
            when,
            "speech",
            f"{h['speaker_name']}{'對我' if directed else ''}說：「{h['utterance']}」"
            + (f"（當時我在{obs.area}）" if not directed else ""),
            IMPORTANCE["spoken_to"] if directed else IMPORTANCE["overheard"],
        )

    for name in obs.new_faces(agent.seen_last_tick):
        agent.memory.add(
            tick, when, "observation", f"在{obs.area}遇到{name}。", IMPORTANCE["new_face"]
        )

    for text in obs.injected:
        agent.memory.add(tick, when, "injected", text, IMPORTANCE["injected"])

    agent.seen_last_tick = [v["name"] for v in obs.visible]
    agent.memory.prune(cfg.memory_cap)


def record_decision(agent, result: dict, tick: int) -> None:
    when = clock_str(tick)
    thought = (result.get("thought") or "").strip()
    if thought:
        agent.memory.add(tick, when, "thought", thought, IMPORTANCE["thought"])
    act = result.get("action") or {}
    if act.get("kind") == "speak" and (act.get("utterance") or "").strip():
        agent.memory.add(
            tick, when, "speech", f"我說：「{act['utterance']}」", IMPORTANCE["own_speech"]
        )
