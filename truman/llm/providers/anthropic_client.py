"""Anthropic provider。

快取用顯式斷點：system 切成兩塊，各打一個 `cache_control`。
斷點 1 涵蓋世界（全 agent 共用），斷點 2 涵蓋世界＋人設（每 agent 一條線）。
上限 4 個斷點，我們用 2 個。
"""

from __future__ import annotations

import json

from anthropic import AsyncAnthropic

from ..base import BaseLLMClient, Call

# 各層的取樣參數。注意：
#   - Haiku 4.5 不支援 output_config.effort，送了會 400。
#   - Sonnet 5 省略 thinking 會跑 adaptive；每 tick 決策不需要，明確關掉。
TIER_PARAMS = {
    "routine": {"thinking": {"type": "disabled"}, "effort": None},
    "dialogue": {"thinking": {"type": "disabled"}, "effort": "low"},
    "reflect": {"thinking": {"type": "adaptive"}, "effort": "high"},
    "judge": {"thinking": {"type": "disabled"}, "effort": "medium"},
}


class AnthropicClient(BaseLLMClient):
    provider = "anthropic"

    def __init__(self, cfg, log, replay=None):
        super().__init__(cfg, log, replay)
        self._client = AsyncAnthropic() if replay is None else None

    @property
    def cache_write_multiplier(self) -> float:
        return 2.0 if self.cfg.cache_ttl == "1h" else 1.25

    def _system(self, blocks: list[str]) -> list[dict]:
        out = []
        for text in blocks:
            block = {"type": "text", "text": text}
            if self.cfg.use_cache:
                block["cache_control"] = {"type": "ephemeral", "ttl": self.cfg.cache_ttl}
            out.append(block)
        return out

    def _output_config(self, tier: str, schema: dict) -> dict:
        cfg = {"format": {"type": "json_schema", "schema": schema}}
        effort = TIER_PARAMS[tier]["effort"]
        if effort:
            cfg["effort"] = effort
        return cfg

    async def _invoke(self, c: Call, model: str):
        resp = await self._client.messages.create(
            model=model,
            max_tokens=c.max_tokens,
            system=self._system(c.system_blocks),
            messages=[{"role": "user", "content": c.user_message}],
            output_config=self._output_config(c.tier, c.schema),
            thinking=TIER_PARAMS[c.tier]["thinking"],
        )
        usage = {
            "inp": resp.usage.input_tokens,
            "out": resp.usage.output_tokens,
            "c_write": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            "c_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        }
        parsed, err = _parse(resp)
        return parsed, err, usage


def _parse(resp) -> tuple[dict | None, str | None]:
    if resp.stop_reason == "refusal":
        return None, f"refusal: {getattr(resp, 'stop_details', None)}"
    if resp.stop_reason == "max_tokens":
        return None, "output truncated (max_tokens)"
    for block in resp.content:
        if block.type == "text":
            try:
                return json.loads(block.text), None
            except json.JSONDecodeError as e:
                return None, f"json decode: {e}"
    return None, "response had no text block"
