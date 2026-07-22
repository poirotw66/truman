"""Token 估算。

離線時只能估。這個估法刻意**低估**——因為它唯一的用途是回答
「我的前綴有沒有跨過最小可快取門檻」，而在那個問題上，
高估會讓你以為有快取、實際上沒有（靜默失效，帳單不會告訴你）。
低估則最多讓你多寫一點內容，沒有壞處。

要真值請用 `python -m truman.cli tokens`，它走 count_tokens 端點。
永遠不要用 tiktoken——那是 OpenAI 的分詞器，對 Claude 是錯的。
"""

from __future__ import annotations

# 保守下界：Claude 的分詞器對常見中文詞會併字，實際多在 0.6–0.8 tok/char。
CJK_TOKENS_PER_CHAR = 0.55
ASCII_TOKENS_PER_CHAR = 0.25


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3000 <= o <= 0x303F  # CJK 標點
        or 0x3400 <= o <= 0x4DBF  # 擴充 A
        or 0x4E00 <= o <= 0x9FFF  # 基本區
        or 0xF900 <= o <= 0xFAFF  # 相容表意
        or 0xFF00 <= o <= 0xFFEF  # 全形
    )


def estimate(text: str) -> int:
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return int(cjk * CJK_TOKENS_PER_CHAR + other * ASCII_TOKENS_PER_CHAR)


async def count_exact(text: str, model: str, provider: str = "anthropic") -> int:
    """真值。需要該 provider 的憑證。"""
    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        resp = await AsyncAnthropic().messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}]
        )
        return resp.input_tokens

    if provider == "gemini":
        from google import genai

        # client 必須有強參考：它一被回收，底下的 httpx 連線就關了。
        client = genai.Client()
        resp = await client.aio.models.count_tokens(model=model, contents=text)
        return resp.total_tokens

    raise ValueError(f"未知的 provider: {provider}")


async def list_models(provider: str) -> list[str]:
    """列出目前實際可用的模型 ID。

    模型 ID 會下架（Gemini 2.0 Flash 已於 2026-06-01 關閉），
    硬編一個過期的 ID 就是 404，所以留一個查證用的出口。
    """
    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic()
        return [m.id async for m in client.models.list()]

    if provider == "gemini":
        from google import genai

        # `models.list()` 回傳的是惰性 pager。client 若沒有強參考，
        # 迭代到一半就會撞上 "Cannot send a request, as the client has been closed."
        client = genai.Client()
        return [m.name for m in client.models.list()]

    raise ValueError(f"未知的 provider: {provider}")
