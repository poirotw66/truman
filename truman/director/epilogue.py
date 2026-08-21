"""戲外導演收場：讀日誌 → 一次 LLM → 寫入 `epilogue` 事件。

角色聽不見這段；回放 outro 離線讀事件即可，不必重跑模擬。
"""

from __future__ import annotations

from pathlib import Path

from ..config import clock_str
from ..llm.client import Call
from ..llm.prompts import EPILOGUE_SYSTEM
from ..llm.schemas import EPILOGUE_SCHEMA
from ..obs.eventlog import EventLog

_MAX_DIGEST_CHARS = 12_000
_MAX_SPEECH = 28
_MAX_MOMENTS = 40


def has_epilogue(run_dir: Path) -> bool:
    return any(ev.get("type") == "epilogue" for ev in EventLog.read(run_dir))


def digest_events(events: list[dict]) -> tuple[str, str, str]:
    """回傳 (scenario, run_id, digest_text)。"""
    scenario = ""
    run_id = ""
    names: dict[str, str] = {}
    for ev in events:
        d = ev.get("data") or {}
        if ev.get("type") == "speech":
            if d.get("speaker") and d.get("speaker_name"):
                names[d["speaker"]] = d["speaker_name"]
        elif ev.get("type") == "death":
            if d.get("agent") and d.get("name"):
                names[d["agent"]] = d["name"]
        elif ev.get("type") == "goal_done" or ev.get("type") == "goal_failed":
            if d.get("agent") and d.get("name"):
                names[d["agent"]] = d["name"]

    speeches: list[str] = []
    moments: list[str] = []
    goals: list[str] = []
    survivors: list[str] = []
    storm = ""
    last_tick = 0
    incomplete = True

    for ev in events:
        ty = ev.get("type")
        d = ev.get("data") or {}
        tick = ev.get("tick")
        if isinstance(tick, int):
            last_tick = max(last_tick, tick)
        when = clock_str(tick) if isinstance(tick, int) else "?"

        if ty == "run_start":
            scenario = d.get("scenario") or scenario
            run_id = d.get("run_id") or run_id
        elif ty == "run_summary":
            incomplete = False
        elif ty == "speech":
            who = d.get("speaker_name") or names.get(d.get("speaker", ""), d.get("speaker") or "?")
            to_id = d.get("to") or ""
            to = names.get(to_id, to_id)
            utter = (d.get("utterance") or d.get("text") or "").strip()
            if utter:
                dest = f"→{to}" if to else ""
                speeches.append(f"[{when}] {who}{dest}：「{utter}」")
        elif ty == "attack":
            moments.append(f"[{when}] 動手：{d.get('line') or d}")
        elif ty == "death":
            moments.append(
                f"[{when}] 死亡：{d.get('name', '?')}（{d.get('killed_by', '不明')}）"
            )
        elif ty == "storm":
            storm = (
                f"暴潮結算 outcome={d.get('outcome', '')}；"
                f"{d.get('text') or d.get('outcome_text') or ''}"
            )
            moments.append(f"[{when}] {storm}")
        elif ty in ("goal_done", "goal_failed"):
            mark = "做到了" if ty == "goal_done" else "沒能做到"
            goals.append(
                f"[{when}] {d.get('name', '?')} {mark}：{d.get('text', '')}"
                + (f"　{d['note']}" if d.get("note") else "")
            )
        elif ty == "snapshot":
            agents = d.get("agents") or {}
            survivors = [
                f"{names.get(aid, aid)}{'✓' if a.get('alive') else '✗'}"
                for aid, a in agents.items()
                if isinstance(a, dict)
            ]

    if len(speeches) > _MAX_SPEECH:
        head = speeches[: _MAX_SPEECH // 2]
        tail = speeches[-(_MAX_SPEECH // 2) :]
        speeches = head + [f"…（中間略過 {len(speeches) - _MAX_SPEECH} 句）…"] + tail
    if len(moments) > _MAX_MOMENTS:
        moments = moments[:_MAX_MOMENTS] + [f"…（另有 {len(moments) - _MAX_MOMENTS} 條）"]

    parts = [
        f"劇本：{scenario or '未知'}",
        f"run_id：{run_id or '未知'}",
        f"最後一刻：tick {last_tick}（{clock_str(last_tick)}）",
        f"場次狀態：{'有 run_summary，可視為收工' if not incomplete else '無 run_summary，可能中斷或不完整'}",
    ]
    if storm:
        parts.append(f"世界結局（機械／戲內）：{storm}")
    if survivors:
        parts.append("最後快照存活：" + "、".join(survivors))
    if goals:
        parts.append("目的結算：\n" + "\n".join(goals))
    if moments:
        parts.append("關鍵時刻：\n" + "\n".join(moments))
    if speeches:
        parts.append("對白摘錄：\n" + "\n".join(speeches))

    text = "\n\n".join(parts)
    if len(text) > _MAX_DIGEST_CHARS:
        text = text[: _MAX_DIGEST_CHARS] + "\n…（紀要截斷）"
    return scenario or "unknown", run_id or "unknown", text


EPILOGUE_MODEL = "gemini-3.7-flash"


def epilogue_call(digest: str, *, run_id: str, tick: int | None = None) -> Call:
    return Call(
        key=f"{tick if tick is not None else 'end'}:{run_id}:epilogue",
        tier="judge",
        system_blocks=[EPILOGUE_SYSTEM],
        user_message=f"以下是本場箱庭實錄的事實紀要。請寫 label / blurb / commentary。\n\n{digest}",
        schema=EPILOGUE_SCHEMA,
        max_tokens=1800,
        model=EPILOGUE_MODEL,
        thinking="low",
    )


def normalize_epilogue(result: dict) -> dict[str, str]:
    label = str(result.get("label") or "").strip()
    blurb = str(result.get("blurb") or "").strip()
    commentary = str(result.get("commentary") or "").strip()
    if not label or not blurb or not commentary:
        raise ValueError("epilogue 缺欄位")
    return {"label": label, "blurb": blurb, "commentary": commentary}


async def generate_epilogue(llm, events: list[dict], *, run_id: str) -> dict[str, str]:
    _scenario, rid, digest = digest_events(events)
    rid = rid or run_id
    last_tick = max((ev.get("tick") or 0) for ev in events) if events else 0
    call = epilogue_call(digest, run_id=rid, tick=last_tick)
    if hasattr(llm, "call"):
        raw = await llm.call(call)
    else:
        batch = await llm.run_batch([call])
        raw = batch[call.key]
    if isinstance(raw, Exception):
        raise raw
    return normalize_epilogue(raw if isinstance(raw, dict) else {})


async def write_epilogue(
    llm,
    log: EventLog,
    run_dir: Path,
    *,
    force: bool = False,
) -> dict[str, str] | None:
    """產生並寫入 epilogue。已有則跳過（除非 force，此時再追加一筆，回放取最後一筆）。"""
    run_dir = Path(run_dir)
    events = list(EventLog.read(run_dir))
    if has_epilogue(run_dir) and not force:
        return None
    if getattr(llm, "provider", None) == "stub":
        payload = {
            "label": "（stub）收工",
            "blurb": "這是 stub 跑出來的場，沒有真正的導演短評。",
            "commentary": "stub 不寫長評。",
            "stub": True,
        }
        log.write("epilogue", payload)
        return payload

    body = await generate_epilogue(llm, events, run_id=run_dir.name)
    log.write("epilogue", {**body, "model": EPILOGUE_MODEL})
    return body
