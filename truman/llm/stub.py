"""離線假 LLM。

用途是驗證管線本身（tick 迴圈、intent 驗證、日誌、report、replay、分支），
不需要憑證也不花錢。輸出是依 key 決定性產生的，所以同一個 seed 每次都一樣。

    python -m truman.cli run --run-id dry --ticks 12 --stub

產出的內容當然沒有任何模擬價值——它只是用來確認水管沒漏。
"""

from __future__ import annotations

from dataclasses import dataclass, field

AREAS = ["咖啡館", "廣場", "報攤", "圖書館", "公園", "海堤", "保險行", "陳家"]

_LINES = [
    "今天天氣真好。",
    "你最近還好嗎？",
    "我昨天好像也聽你講過這句。",
    "沒事，我隨口問問。",
]


@dataclass
class StubLLM:
    cfg: object
    log: object
    n: int = 0
    usage_by_tier: dict = field(default_factory=dict)

    provider = "stub"

    def stats(self) -> dict:
        return {"_provider": "stub", "_total_cost_usd": 0.0}

    def total_cost(self) -> float:
        return 0.0

    async def run_batch(self, calls) -> dict:
        out = {}
        for c in calls:
            self.n += 1
            out[c.key] = self._fake(c)
            self.log.write("llm_call", {"key": c.key, "tier": c.tier,
                                        "output": out[c.key], "stub": True})
        return out

    def _fake(self, c) -> dict:
        who = c.key.split(":")[1]
        if c.key.endswith(":reflect"):
            return {"insights": [f"（stub）{who} 最近有點反常。"],
                    "beliefs": ["（stub）這裡的日子太規律了。"]}
        if c.key.endswith(":awareness"):
            return {"score": min(10, self.n // 8), "evidence": ["（stub）"],
                    "rationale": "stub 評分，無意義。"}

        i = self.n
        if i % 5 == 2:
            return {
                "thought": "（stub）總覺得哪裡不對勁。",
                "action": {"kind": "speak", "target_agent": "",
                           "utterance": _LINES[i % len(_LINES)],
                           "target_area": "", "object": ""},
                "plan": "（stub）找人聊聊。",
            }
        if i % 5 == 4:
            return {
                "thought": "（stub）先待著。",
                "action": {"kind": "interact", "object": "翻報紙",
                           "target_area": "", "target_agent": "", "utterance": ""},
                "plan": "（stub）待著。",
            }
        area = AREAS[i % len(AREAS)]
        return {
            "thought": "（stub）該走了。",
            "action": {"kind": "move_to", "target_area": area,
                       "target_agent": "", "utterance": "", "object": ""},
            "plan": f"（stub）去{area}。",
        }
