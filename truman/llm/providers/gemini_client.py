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

# thinking_level：SDK 列了 minimal|low|medium|high，但每個模型只接受子集。
# 目前預設模型是 gemini-3.1-flash-lite，實跑上 low / high 都可用；先用 low 壓成本。
# 若使用 2.5-flash-lite，可能要另外覆寫到 high。
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

            # http_options 的 timeout 是毫秒。base 那層的 wait_for 是保險，
            # 這一層才是真的把吊住的連線斷掉（wait_for 只取消 task）。
            self._client = genai.Client(
                http_options={"timeout": int(getattr(cfg, "call_timeout", 180.0) * 1000)}
            )

    def _gen_config(self, c: Call, model: str) -> dict:
        """每個 agent 可以自帶 thinking_level 與 temperature（AgentState.llm）。"""
        level = c.thinking or self._thinking(c.tier)
        cfg = {
            "thinking_level": level,
            "max_output_tokens": c.max_tokens,
        }
        if c.temperature is not None:
            cfg["temperature"] = c.temperature
        return cfg

    def _thinking(self, tier: str) -> str:
        override = getattr(self.cfg, "gemini_thinking", None) or {}
        return override.get(tier, DEFAULT_THINKING[tier])

    async def _invoke(self, c: Call, model: str):
        # 世界在前、人設在後——隱式快取靠的就是這個順序穩定。
        system_instruction = "\n\n".join(c.system_blocks)

        async def once(call: Call):
            try:
                resp = await self._client.aio.interactions.create(
                    model=model,
                    system_instruction=system_instruction,
                    input=call.user_message,
                    store=False,
                    generation_config=self._gen_config(call, model),
                    response_format=text_json_format(call.schema),
                )
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                if "thinking budget" in msg or "thinking level" in msg:
                    raise ValueError(
                        f"{model} 不接受 thinking_level="
                        f"{(call.thinking or self._thinking(call.tier))!r}。"
                        f"請改用這個模型支援的等級（例如 3.1-flash-lite 用 low/high；"
                        f"2.5-flash-lite 用 minimal/low/high，不要用 medium）。"
                        f"原始錯誤：{e}"
                    ) from e
                # 安全過濾擋下的 prompt：重試同一份通常沒用，標清楚讓上層略過。
                if "blocked" in msg or "sensitive" in msg:
                    raise ValueError(
                        f"輸入被安全過濾擋下（{type(e).__name__}）。"
                        f"這次 reflection／決策略過，不重試同一份內容。原始錯誤：{e}"
                    ) from e
                raise
            return resp

        resp = await once(c)
        parsed, err = _parse(resp)
        usage = _usage(resp)
        # thinking 吃掉 max_output_tokens 時 JSON 會切半——加額度再試一次。
        if err and "輸出被截斷" in err and c.max_tokens < 8000:
            from dataclasses import replace

            bumped = replace(c, max_tokens=min(8000, c.max_tokens * 2))
            self.log.write("llm_retry", {
                "key": c.key, "model": model, "attempt": 1, "of": 2,
                "wait_s": 0, "reason": "truncated_output",
                "error": err[:200],
            })
            resp2 = await once(bumped)
            parsed2, err2 = _parse(resp2)
            usage2 = _usage(resp2)
            # 合併兩次用量，成功與否都算錢。
            for k in ("inp", "out", "c_write", "c_read"):
                usage[k] = usage.get(k, 0) + usage2.get(k, 0)
            if parsed2 is not None:
                return parsed2, None, usage
            return None, err2 or err, usage
        return parsed, err, usage


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
    text = _strip_fence(text)
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


def _strip_fence(text: str) -> str:
    """模型有時會包 ```json ... ```；schema 已要求 JSON，但仍要防呆。"""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
