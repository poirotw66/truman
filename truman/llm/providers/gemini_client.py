"""Gemini provider（Interactions API）。

和 Anthropic 的三個結構性差異：

1. **沒有顯式快取斷點。** `system_instruction` 只能是單一字串，所以世界與人設是接起來
   送的，由服務端自己找最長共同前綴（隱式快取，2.5 以上自動啟用）。
   接的順序仍然重要：世界在前（全 agent 共用）、人設在後（每 agent 一份）。
2. **沒有寫入溢價、沒有儲存費。** Interactions API 只支援隱式快取，
   explicit cache（`client.caches`）在這個 API 上不可用。
3. **`store=False`。** 每個 tick 都是獨立呼叫，對話狀態由我們自己的 memory stream 管，
   不要讓服務端也存一份。

`response_format` 的形狀有個很貴的陷阱（實測確認，google-genai 2.13）：

  Interactions 的 ResponseFormatParam 是**格式物件本身的 union**，不是包一層：
      {"type": "text", "mime_type": "application/json", "schema_": SCHEMA}   ✅
      {"text": {"mimeType": ..., "schema": ...}}                             ❌ 靜默忽略
      {"type": "text", "mimeType": ..., "schema": ...}                       ❌ 400

  第二種是 `google.genai.types.ResponseFormat` 的形狀——那是 generate_content 用的，
  和 Interactions 的同名但不同構。傳錯不會報錯，只會**安靜地不生效**：
  模型改成自由文字輸出，然後你在 JSON 解析那一步才發現，而 token 已經燒掉了。
  欄位名用 TypedDict 的 `mime_type` / `schema_`，別名轉換由 SDK 負責。

usage 欄位（SDK 內省確認）：
  usage.total_input_tokens / total_output_tokens / total_cached_tokens / total_thought_tokens
"""

from __future__ import annotations

import json

from ..base import BaseLLMClient, Call

# thinking_level 合法值：minimal | low | medium | high（SDK 內省確認）
TIER_PARAMS = {
    "routine": {"thinking_level": "low"},
    "dialogue": {"thinking_level": "low"},
    "reflect": {"thinking_level": "high"},
    "judge": {"thinking_level": "low"},
}


class GeminiClient(BaseLLMClient):
    provider = "gemini"
    cache_write_multiplier = 0.0  # 隱式快取沒有寫入成本

    def __init__(self, cfg, log, replay=None):
        super().__init__(cfg, log, replay)
        self._client = None
        if replay is None:
            from google import genai

            self._client = genai.Client()

    async def _invoke(self, c: Call, model: str):
        # 世界在前、人設在後——隱式快取靠的就是這個順序穩定。
        system_instruction = "\n\n".join(c.system_blocks)

        resp = await self._client.aio.interactions.create(
            model=model,
            system_instruction=system_instruction,
            input=c.user_message,
            store=False,
            generation_config={
                "thinking_level": TIER_PARAMS[c.tier]["thinking_level"],
                "max_output_tokens": c.max_tokens,
            },
            response_format=text_json_format(c.schema),
        )
        return (*_parse(resp), _usage(resp))


def text_json_format(schema: dict) -> dict:
    """Interactions 的 response_format：格式物件本身，不是 {"text": {...}}。"""
    return {"type": "text", "mime_type": "application/json", "schema_": schema}


def _usage(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    total_in = getattr(u, "total_input_tokens", 0) or 0
    total_out = getattr(u, "total_output_tokens", 0) or 0
    cached = getattr(u, "total_cached_tokens", 0) or 0
    thoughts = getattr(u, "total_thought_tokens", 0) or 0
    grand = getattr(u, "total_tokens", 0) or 0

    # total_input_tokens 含快取部分，所以未快取的量要扣掉，否則會重複計價。
    uncached_in = max(0, total_in - cached)

    # thought tokens 以輸出計價，但不確定 total_output_tokens 有沒有已經包含它。
    # 用 total_tokens 反推：包含了就不重複加，沒包含就補上。
    if grand and abs(grand - (total_in + total_out)) > max(2, 0.01 * grand):
        total_out += thoughts

    return {"inp": uncached_in, "out": total_out, "c_write": 0, "c_read": cached}


def _parse(resp) -> tuple[dict | None, str | None]:
    status = getattr(resp, "status", None)
    text = getattr(resp, "output_text", None)
    if not text:
        return None, f"empty output_text (status={status})"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"json decode: {e} | head={text[:120]!r}"
