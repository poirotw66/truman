"""Provider 工廠。

模擬的其餘部分完全不知道背後是哪一家——它只看得到 `Call` 和
`run_batch()`。要加第三家 provider 就多寫一個 `BaseLLMClient` 子類別。
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import BaseLLMClient, Call, Usage  # noqa: F401 - 對外沿用舊的匯入路徑

PROVIDER_CLASSES = {
    "anthropic": ("truman.llm.providers.anthropic_client", "AnthropicClient"),
    "gemini": ("truman.llm.providers.gemini_client", "GeminiClient"),
}


def make_client(cfg, log, replay: dict[str, dict] | None = None) -> BaseLLMClient:
    import importlib

    try:
        mod_name, cls_name = PROVIDER_CLASSES[cfg.provider]
    except KeyError:
        raise ValueError(
            f"未知的 provider {cfg.provider!r}，可選：{sorted(PROVIDER_CLASSES)}"
        ) from None
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as e:
        raise ImportError(
            f"{cfg.provider} 的相依套件沒裝好（{e}）。\n"
            f"  anthropic → pip install anthropic\n"
            f"  gemini    → pip install google-genai"
        ) from e
    return getattr(mod, cls_name)(cfg=cfg, log=log, replay=replay)


def build_replay_index(events_path: Path) -> dict[str, dict]:
    """從既有 run 的 events.jsonl 重建 LLM 輸出索引，用於零成本重放。

    索引的 key 不含 provider，所以 Anthropic 跑出來的軌跡可以拿去餵 Gemini 的
    重放，反之亦然——重放本來就不呼叫任何 API。
    """
    index: dict[str, dict] = {}
    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("type") == "llm_call" and ev["data"].get("output"):
                index[ev["data"]["key"]] = ev["data"]["output"]
    return index
