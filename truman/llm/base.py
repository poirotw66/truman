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
    # --- 每個 agent 自己的模型設定（AgentState.llm）。None = 照分層預設走。---
    # 讓不同角色掛不同模型／溫度是刻意留的實驗手段：同一個劇本，只換一個人的腦袋。
    model: str | None = None
    temperature: float | None = None
    thinking: str | None = None


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


_TRANSIENT = (
    "timeout", "timed out", "connection", "connect", "reset by peer",
    "temporarily unavailable", "unavailable", "overloaded", "rate limit",
    "resource_exhausted", "too many requests", "429",
    "500", "502", "503", "504", "internal error", "bad gateway",
)


def _transient(e: Exception) -> bool:
    """字串比對是刻意的：兩家 SDK 的例外型別不同，也不保證跨版本穩定。"""
    return any(m in str(e).lower() for m in _TRANSIENT)


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
        self.warmup_note: set[str] = set()

    # ------------------------------------------------------------ 待實作
    async def _invoke(self, c: Call, model: str) -> tuple[dict | None, str | None, dict]:
        """回傳 (parsed_output, error, usage_dict)。usage_dict 的鍵同 Usage.add()。"""
        raise NotImplementedError

    # ------------------------------------------------------------ 統計
    # 分桶的鍵是「層」，但有人自帶模型時就變成「層·模型」——不然那些呼叫會被
    # 按分層預設模型的價目計價，帳直接算錯。
    def _bucket(self, tier: str, model: str) -> str:
        return tier if model == self.cfg.models.get(tier) else f"{tier}·{model}"

    def _model_of(self, bucket: str) -> str:
        tier, sep, model = bucket.partition("·")
        return model if sep else self.cfg.models[tier]

    def _usage(self, bucket: str) -> Usage:
        return self.usage_by_tier.setdefault(bucket, Usage())

    def total_cost(self) -> float:
        return sum(
            u.cost(self.cfg.price(self._model_of(b)), self.cache_write_multiplier)
            for b, u in self.usage_by_tier.items()
        )

    def stats(self) -> dict:
        out: dict = {"_provider": self.provider}
        for tier, u in self.usage_by_tier.items():
            model = self._model_of(tier)
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

    # ------------------------------------------------------------ 逾時與重試
    async def _invoke_guarded(self, c: Call, model: str):
        """給 `_invoke` 套上逾時與重試。

        兩家 SDK 的預設都可能讓一條連線無限期吊住，而 `run_batch` 的 gather 要等
        整批到齊——一個請求不回來，整場模擬就停在那裡，還不會報錯。所以逾時要在
        這一層強制，不能只靠 provider 的設定。

        只重試「等一下可能就好了」的錯（逾時、連線、429、5xx）。schema 錯、
        thinking budget 不合法這種重試幾次都一樣的，直接往上丟。
        """
        timeout = getattr(self.cfg, "call_timeout", 180.0)
        attempts = max(1, getattr(self.cfg, "max_retries", 3))

        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(self._invoke(c, model), timeout)
            except Exception as e:  # noqa: BLE001
                last = attempt == attempts
                timed_out = isinstance(e, (asyncio.TimeoutError, TimeoutError))
                if last or not (timed_out or _transient(e)):
                    raise
                delay = min(2.0 ** (attempt - 1), 8.0)
                self.log.write("llm_retry", {
                    "key": c.key, "model": model, "attempt": attempt,
                    "of": attempts, "wait_s": delay,
                    "reason": "timeout" if timed_out else type(e).__name__,
                    "error": str(e)[:200],
                })
                await asyncio.sleep(delay)

    # ------------------------------------------------------------ 送出
    async def preflight(self, world=None) -> list[tuple[str, str, str]]:
        """開跑前把每一組（模型, thinking_level）各試一次。

        回傳失敗的那幾組 [(model, thinking, 錯誤訊息)]；全過就是空清單。

        為什麼要這個：`j3` 跑了 96 拍、576 次呼叫**全部失敗**，只因為
        `gemini-2.5-flash-lite` 不接受 `thinking_level=medium`。32 秒就「跑完」了，
        零對話零意圖，而且要等全部跑完才回報。那次是 400 錯誤沒花到錢，
        但同樣的設定錯誤配上一個會計費的模型，就是燒掉一整天換一份空日誌。

        只試「真的會用到」的組合：分層路由的四層，加上每個 agent 自己掛的模型。
        每次幾十個 token，比事後才發現便宜太多。
        """
        if self.replay is not None:
            return []
        # 要照**解析後**的 thinking_level 去重，不能只看模型。四層預設同一個模型，
        # 但 thinking 不同（routine/dialogue/judge 是 low、reflect 是 high）——
        # 只按模型去重的話會塌成一組，reflect 那個 high 從來不會被試到，
        # 而那正好就是 j3 死掉的那一類錯誤。
        resolve = getattr(self, "_thinking", None)  # Gemini 才有；Anthropic 回 None
        combos: dict[tuple[str, str | None], str] = {}
        for tier in ("routine", "dialogue", "reflect", "judge"):
            level = resolve(tier) if resolve else None
            combos.setdefault((self.cfg.models[tier], level), tier)
        for a in (world.agents.values() if world is not None else ()):
            spec = a.llm or {}
            if spec.get("model") or spec.get("thinking"):
                model = spec.get("model") or self.cfg.models["routine"]
                level = spec.get("thinking") or (resolve("routine") if resolve else None)
                combos.setdefault((model, level), "routine")

        probe_schema = {
            "type": "object",
            "properties": {"ok": {"type": "string"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        bad: list[tuple[str, str, str]] = []
        for (model, thinking), tier in combos.items():
            c = Call(
                key=f"preflight:{model}:{thinking or tier}",
                tier=tier,
                system_blocks=["你是一個測試用的回聲程式。"],
                user_message="回一個 JSON：{\"ok\": \"1\"}",
                schema=probe_schema,
                max_tokens=self.cfg.max_output_tokens,
                model=model,
                thinking=thinking,
            )
            try:
                await self._invoke_guarded(c, model)
            except Exception as e:  # noqa: BLE001 —— 任何失敗都要報，不能只認某幾種
                bad.append((model, thinking or f"（{tier} 層預設）", str(e)))
        self.log.write("preflight", {
            "checked": [{"model": m, "thinking": th} for (m, th) in combos],
            "failed": [{"model": m, "thinking": th, "error": err} for m, th, err in bad],
        })
        return bad

    async def call(self, c: Call) -> dict:
        if self.replay is not None:
            rec = self.replay.get(c.key)
            if rec is None:
                raise KeyError(f"replay 記錄缺少 {c.key}；這條軌跡與原始 run 不一致")
            self.log.write(
                "llm_call", {"key": c.key, "tier": c.tier, "output": rec, "replayed": True}
            )
            return rec

        model = c.model or self.cfg.models[c.tier]
        self._warn_short_prefix(c.system_blocks, model)
        parsed, err, usage = await self._invoke_guarded(c, model)
        self._usage(self._bucket(c.tier, model)).add(**usage)

        self.log.write(
            "llm_call",
            {
                "key": c.key,
                "tier": c.tier,
                "provider": self.provider,
                "model": model,
                "temperature": c.temperature,
                "usage": usage,
                "output": parsed,
                "error": err,
            },
        )
        if err:
            raise ValueError(f"{c.key}: {err}")
        return parsed

    def _worth_warming(self, model: str, sample: Call) -> bool:
        """前綴進不了快取，暖機就只是白白多花一趟來回。

        暖機的用意是「先送一個把共同前綴寫進快取，其餘才並行」。但前綴低於門檻時
        快取根本不會生效（靜默失效），這一趟就純粹是每個 tick 多一次序列往返——
        96 個 tick 累積起來很可觀。實測 jianghu 的前綴約 2215 tokens，
        而 3.1 系列的門檻是 4096，所以這裡會直接全部並行。
        """
        if not self.cfg.use_cache:
            return False
        from .tokens import estimate

        approx = estimate("".join(sample.system_blocks))
        ok = approx >= self.cfg.cache_min(model)
        if not ok and model not in self.warmup_note:
            self.warmup_note.add(model)
            self.log.write("warmup_skipped", {
                "model": model, "approx_prefix_tokens": approx,
                "min_cacheable": self.cfg.cache_min(model),
                "note": "前綴進不了快取，跳過循序暖機，整批並行送出。",
            })
        return ok

    async def run_batch(self, calls: list[Call]) -> dict[str, dict | Exception]:
        """依模型分組；能快取的那一組才做循序暖機，其餘直接並行。

        兩家的快取都要等第一個回應開始產生之後才可讀，所以同一 tick 平行送 N 個
        共享前綴的請求會 N 個全部落空——**前提是前綴真的進得了快取**。
        進不了就沒有暖機的必要（見 `_worth_warming`）。
        每個人可以自帶模型，所以要分組：拿 A 模型暖機救不了 B 模型的前綴。
        """
        results: dict[str, dict | Exception] = {}
        if not calls:
            return results

        sem = asyncio.Semaphore(self.cfg.max_concurrency)

        async def one(c: Call):
            async with sem:
                try:
                    return c.key, await self.call(c)
                except Exception as e:  # noqa: BLE001 - 單一 agent 失敗不該中斷整個 tick
                    return c.key, e

        groups: dict[str, list[Call]] = {}
        for c in calls:
            groups.setdefault(c.model or self.cfg.models[c.tier], []).append(c)

        async def run_group(model: str, cs: list[Call]):
            out = []
            if len(cs) > 1 and self._worth_warming(model, cs[0]):
                out.append(await one(cs[0]))      # 先送一個，把共同前綴寫進快取
                cs = cs[1:]
            out.extend(await asyncio.gather(*(one(c) for c in cs)))
            return out

        for pairs in await asyncio.gather(*(run_group(m, cs) for m, cs in groups.items())):
            for key, val in pairs:
                results[key] = val
        return results
