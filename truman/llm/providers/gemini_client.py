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

# thinking_level 合法值：minimal | low | medium | high（SDK 內省確認）。
#
# ⚠ 各層級換算出的 thinking budget 下限**隨模型而異**：
#   gemini-3.1-flash-lite  "low" 可用
#   gemini-2.5-flash-lite  "low" → budget 256，低於該模型下限 512，直接 400
# 換模型時如果撞到 "thinking budget N is invalid"，就把該層調高一級
# （`--thinking medium`，或改 SimConfig.gemini_thinking）。
DEFAULT_THINKING = {
    "routine": "low",
    "dialogue": "low",
    "reflect": "high",
    "judge": "low",
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

    def _thinking(self, tier: str) -> str:
        override = getattr(self.cfg, "gemini_thinking", None) or {}
        return override.get(tier, DEFAULT_THINKING[tier])

    async def _invoke(self, c: Call, model: str):
        # 世界在前、人設在後——隱式快取靠的就是這個順序穩定。
        system_instruction = "\n\n".join(c.system_blocks)

        try:
            resp = await self._client.aio.interactions.create(
                model=model,
                system_instruction=system_instruction,
                input=c.user_message,
                store=False,
                generation_config={
                    "thinking_level": self._thinking(c.tier),
                    "max_output_tokens": c.max_tokens,
                },
                response_format=text_json_format(c.schema),
            )
        except Exception as e:  # noqa: BLE001
            if "thinking budget" in str(e):
                raise ValueError(
                    f"{model} 不接受 thinking_level={self._thinking(c.tier)!r}"
                    f"（各模型的 budget 下限不同）。調高一級再試："
                    f" --thinking medium。原始錯誤：{e}"
                ) from e
            raise
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
        # 被 max_output_tokens 切斷時，JSON 會停在半路。這和「模型吐了非 JSON」
        # 是兩種完全不同的問題，錯誤訊息要分得出來，否則會往錯的方向查。
        if not text.rstrip().endswith(("}", "]")):
            return None, (
                f"輸出被截斷（{len(text)} 字元，結尾不完整）——多半是 max_tokens 不夠，"
                f"reflect 這類長輸出尤其容易撞到。原始錯誤：{e}"
            )
        return None, f"json decode: {e} | head={text[:120]!r}"
