"""Provider 無關的共用層：請求物件、用量統計、批次排程、replay。

兩家 provider 的差異被壓在 `_invoke()` 這一個方法裡；
節流暖機、成本統計、日誌、replay 都是共用的，不該各寫一份。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class Call:
    """一次待送出的請求。provider 無關。

    system_blocks 是**有序**的穩定層級，越前面越穩定：
      [0] 世界（全 agent 共用）
      [1] 人設＋信念（每 agent 一份，只在 reflection 變動）

    Anthropic 會把每一塊各打一個 cache_control 斷點；
    Gemini 的 system_instruction 只能是單一字串，所以會接起來，
    由服務端自己找最長共同前綴（隱式快取）。兩邊的順序需求是一樣的。
    """

    key: str
    tier: str
    system_blocks: list[str]
    user_message: str
    schema: dict
    max_tokens: int = 900


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0  # 未快取、全價的輸入
    output_tokens: int = 0
    cache_write: int = 0  # Anthropic 專屬；Gemini 隱式快取沒有寫入成本
    cache_read: int = 0

    def add(
        self, *, inp: int = 0, out: int = 0, c_write: int = 0, c_read: int = 0
    ) -> None:
        self.calls += 1
        self.input_tokens += inp or 0
        self.output_tokens += out or 0
        self.cache_write += c_write or 0
        self.cache_read += c_read or 0

    def cost(self, prices: tuple[float, float, float], write_mult: float) -> float:
        p_in, p_out, p_cached = prices
        return (
            self.input_tokens * p_in
            + self.cache_write * p_in * write_mult
            + self.cache_read * p_cached
            + self.output_tokens * p_out
        ) / 1_000_000

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write": self.cache_write,
            "cache_read": self.cache_read,
        }


class BaseLLMClient:
    """子類別只需要實作 `_invoke()` 與 `cache_write_multiplier`。"""

    provider = "base"
    cache_write_multiplier = 0.0

    def __init__(self, cfg, log, replay: dict[str, dict] | None = None):
        self.cfg = cfg
        self.log = log
        self.replay = replay
        self.usage_by_tier: dict[str, Usage] = {}
        self.warned_prefix: set[str] = set()

    # ------------------------------------------------------------ 待實作
    async def _invoke(self, c: Call, model: str) -> tuple[dict | None, str | None, dict]:
        """回傳 (parsed_output, error, usage_dict)。usage_dict 的鍵同 Usage.add()。"""
        raise NotImplementedError

    # ------------------------------------------------------------ 統計
    def _usage(self, tier: str) -> Usage:
        return self.usage_by_tier.setdefault(tier, Usage())

    def total_cost(self) -> float:
        return sum(
            u.cost(self.cfg.price(self.cfg.models[t]), self.cache_write_multiplier)
            for t, u in self.usage_by_tier.items()
        )

    def stats(self) -> dict:
        out: dict = {"_provider": self.provider}
        for tier, u in self.usage_by_tier.items():
            model = self.cfg.models[tier]
            cached = u.cache_read + u.cache_write
            out[tier] = {
                "model": model,
                **u.to_dict(),
                "cache_hit_rate": round(u.cache_read / cached, 3) if cached else 0.0,
                "cost_usd": round(
                    u.cost(self.cfg.price(model), self.cache_write_multiplier), 4
                ),
            }
        out["_total_cost_usd"] = round(self.total_cost(), 4)
        return out

    # ------------------------------------------------------------ 前綴檢查
    def _warn_short_prefix(self, blocks: list[str], model: str) -> None:
        from .tokens import estimate

        approx = estimate("".join(blocks))
        floor = self.cfg.cache_min(model)
        if self.cfg.use_cache and approx < floor and model not in self.warned_prefix:
            self.warned_prefix.add(model)
            self.log.write(
                "cache_warning",
                {
                    "provider": self.provider,
                    "model": model,
                    "approx_prefix_tokens": approx,
                    "min_cacheable": floor,
                    "note": "前綴低於門檻，快取會靜默失效（不會多收錢，只是沒有效益）。",
                },
            )

    # ------------------------------------------------------------ 送出
    async def call(self, c: Call) -> dict:
        if self.replay is not None:
            rec = self.replay.get(c.key)
            if rec is None:
                raise KeyError(f"replay 記錄缺少 {c.key}；這條軌跡與原始 run 不一致")
            self.log.write(
                "llm_call", {"key": c.key, "tier": c.tier, "output": rec, "replayed": True}
            )
            return rec

        model = self.cfg.models[c.tier]
        self._warn_short_prefix(c.system_blocks, model)
        parsed, err, usage = await self._invoke(c, model)
        self._usage(c.tier).add(**usage)

        self.log.write(
            "llm_call",
            {
                "key": c.key,
                "tier": c.tier,
                "provider": self.provider,
                "model": model,
                "usage": usage,
                "output": parsed,
                "error": err,
            },
        )
        if err:
            raise ValueError(f"{c.key}: {err}")
        return parsed

    async def run_batch(self, calls: list[Call]) -> dict[str, dict | Exception]:
        """循序暖機 + 並行。

        兩家的快取都要等第一個回應開始產生之後才可讀，所以同一 tick 平行送 N 個
        共享前綴的請求會 N 個全部落空。先送一個、等它回來，其餘才並行。
        """
        results: dict[str, dict | Exception] = {}
        if not calls:
            return results

        first = calls[0]
        try:
            results[first.key] = await self.call(first)
        except Exception as e:  # noqa: BLE001 - 單一 agent 失敗不該中斷整個 tick
            results[first.key] = e

        rest = calls[1:]
        if not rest:
            return results

        sem = asyncio.Semaphore(self.cfg.max_concurrency)

        async def one(c: Call):
            async with sem:
                try:
                    return c.key, await self.call(c)
                except Exception as e:  # noqa: BLE001
                    return c.key, e

        for key, val in await asyncio.gather(*(one(c) for c in rest)):
            results[key] = val
        return results
