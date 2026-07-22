"""全域設定：時間制度、模型分層、認知門檻。"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 時間制度
TICK_MINUTES = 10
DAY_START_MIN = 6 * 60  # 06:00 起床
DAY_END_MIN = 22 * 60  # 22:00 收工
TICKS_PER_DAY = (DAY_END_MIN - DAY_START_MIN) // TICK_MINUTES  # 96


def sim_clock(tick: int) -> tuple[int, int, int]:
    """tick -> (第幾天, 時, 分)。第 1 天從 06:00 開始。"""
    day, within = divmod(tick, TICKS_PER_DAY)
    minute_of_day = DAY_START_MIN + within * TICK_MINUTES
    return day + 1, minute_of_day // 60, minute_of_day % 60


def clock_str(tick: int) -> str:
    day, hh, mm = sim_clock(tick)
    return f"第{day}天 {hh:02d}:{mm:02d}"


# ---------------------------------------------------------------- 模型分層
#
# 兩家 provider 的分層形狀一樣（routine / dialogue / reflect / judge），
# 但快取機制不同：
#
#   Anthropic  顯式斷點（cache_control），system 可切成多塊，各自打斷點。
#              寫入要付 1.25×（5m）或 2×（1h）溢價，讀取 0.1×。
#   Gemini     隱式快取（Interactions API 只支援這種），自動比對最長共同前綴。
#              沒有寫入溢價、沒有儲存費，但 system_instruction 只能是一整個字串，
#              所以沒有「斷點」可言——共同前綴由服務端自己找。
#
# 兩邊共通的是：前綴低於門檻就靜默失效，帳單上看不出來。
#
# 價格為每 MTok 美元 (輸入, 輸出, 快取輸入)。查證日期 2026-07-23。
# 價格與模型 ID 都會變，跑之前請用 `python -m truman.cli models` 對一次。

PROVIDERS = {
    "anthropic": {
        "models": {
            "routine": "claude-haiku-4-5",  # 例行決策：走路、發呆、日程推進
            "dialogue": "claude-sonnet-5",  # 對話、社交決策、主角的每一步
            "reflect": "claude-opus-4-8",  # 把散落觀察合成高階信念
            "judge": "claude-sonnet-5",  # 覺察評分
        },
        "prices": {
            "claude-opus-4-8": (5.0, 25.0, 0.5),
            "claude-opus-4-7": (5.0, 25.0, 0.5),
            "claude-sonnet-5": (3.0, 15.0, 0.3),
            "claude-sonnet-4-6": (3.0, 15.0, 0.3),
            "claude-haiku-4-5": (1.0, 5.0, 0.1),
        },
        # 最小可快取前綴（tokens）
        "cache_min": {
            "claude-opus-4-8": 4096,
            "claude-opus-4-7": 4096,
            "claude-haiku-4-5": 4096,
            "claude-sonnet-5": 2048,
            "claude-sonnet-4-6": 2048,
        },
    },
    "gemini": {
        # 全層統一 gemini-3.1-flash-lite（使用者指定）。
        # 統一單一模型的副作用：四層共用同一條快取線，前綴命中率反而更好。
        # 代價是 reflect 也跑在 flash-lite 上——那是最吃推理深度的一層，
        # 若 insights 顯得空泛，優先把它換成 gemini-3.5-flash 或 gemini-3.1-pro-preview：
        #   python -m truman.cli --provider gemini --model reflect=gemini-3.5-flash run ...
        "models": {
            "routine": "gemini-3.1-flash-lite",
            "dialogue": "gemini-3.1-flash-lite",
            "reflect": "gemini-3.1-flash-lite",
            "judge": "gemini-3.1-flash-lite",
        },
        "prices": {
            "gemini-3.6-flash": (1.50, 7.50, 0.15),
            "gemini-3.5-flash": (1.50, 9.00, 0.15),
            "gemini-3.5-flash-lite": (0.30, 2.50, 0.03),
            "gemini-3.1-flash-lite": (0.25, 1.50, 0.025),
            "gemini-3.1-pro-preview": (2.00, 12.00, 0.20),  # ≤200k 級距
            "gemini-2.5-pro": (1.25, 10.00, 0.125),  # ≤200k 級距
            "gemini-2.5-flash": (0.30, 2.50, 0.03),
            "gemini-2.5-flash-lite": (0.10, 0.40, 0.01),
        },
        # 官方文件只列了這四個；其餘依世代推定，標 * 者為推定值，請自行驗證。
        "cache_min": {
            "gemini-3.5-flash": 4096,
            "gemini-3.1-pro-preview": 4096,
            "gemini-2.5-flash": 2048,
            "gemini-2.5-pro": 2048,
            "gemini-3.6-flash": 4096,  # *
            "gemini-3.5-flash-lite": 4096,  # *
            "gemini-3.1-flash-lite": 4096,  # *
            "gemini-2.5-flash-lite": 2048,  # *
        },
    },
}

DEFAULT_PROVIDER = "anthropic"

# 為什麼 Anthropic 的 routine 層留在 Haiku 而不換 Sonnet：
#
#   Haiku 未快取 : (P+V)·1  + O·5       （P=前綴, V=每 tick 變動, O=輸出，$/MTok）
#   Sonnet 快取  :  P·0.3 + V·3 + O·15
#
# Sonnet 划算的條件是 O·10 < P·0.7 − V·2，V=400 / O=200 時約為 P > 4000。
# 但 P 一旦到 4096，Haiku 自己也開始快取，所以 Haiku 一路領先。
# Gemini 那邊的取捨相反（2.5 系列門檻低到我們跨得過），所以預設就選會快取的那一檔。


def provider_models(provider: str) -> dict:
    return dict(PROVIDERS[provider]["models"])


def cache_min(provider: str, model: str) -> int:
    """未列出的模型一律當 4096（保守：假設較難命中）。"""
    return PROVIDERS[provider]["cache_min"].get(model, 4096)


def price(provider: str, model: str) -> tuple[float, float, float]:
    return PROVIDERS[provider]["prices"].get(model, (0.0, 0.0, 0.0))


@dataclass
class SimConfig:
    # --- provider 與模型路由（見上方成本推導）---
    provider: str = DEFAULT_PROVIDER
    models: dict = field(default_factory=dict)  # 留空則採用該 provider 的預設
    # Gemini 專屬：每層的 thinking_level。留空則用 provider 預設。
    # 合法下限隨模型而異——見 gemini_client.DEFAULT_THINKING 的註解。
    gemini_thinking: dict = field(default_factory=dict)

    # --- 感知 ---
    vision_radius: int = 5  # Chebyshev 距離內可見
    hearing_radius: int = 3  # 說話可被聽見的半徑
    move_speed: int = 3  # 每 tick 可走幾格

    # --- 認知節流（成本的最大槓桿）---
    forced_think_interval: int = 6  # 就算無事發生，每 N tick 也強制思考一次
    max_output_tokens: int = 900

    # --- 記憶 ---
    retrieval_k: int = 8  # 每次思考檢索幾條 episodic memory
    recency_halflife: float = 36.0  # 近時性半衰期（ticks）
    w_recency: float = 1.0
    w_importance: float = 1.0
    w_relevance: float = 1.4
    memory_cap: int = 400  # 每個 agent 保留的 episodic memory 上限

    # --- reflection ---
    reflection_threshold: int = 60  # 累積 importance 超過就觸發
    reflection_window: int = 40  # 送進 reflection 的最近記憶數
    max_beliefs: int = 12  # 穩定信念摘要保留條數（會進快取區塊）

    # --- 導演 / 觀測 ---
    judge_interval: int = 24  # 每 N tick 用 LLM 評一次主角覺察度
    checkpoint_interval: int = 12  # 每 N tick 存一次 checkpoint

    # --- LLM ---
    use_cache: bool = True
    cache_ttl: str = "5m"  # "5m" (1.25x 寫入) 或 "1h" (2x 寫入)
    protagonist_min_tier: str = "dialogue"  # 主角至少用這一層
    max_concurrency: int = 8

    # --- 覺察偵測關鍵詞（樣式法，LLM judge 之外的廉價哨兵）---
    suspicion_markers: list[str] = field(
        default_factory=lambda: [
            "不對勁", "怪怪的", "太巧", "巧合", "被監視", "在看我", "假的",
            "安排好", "演戲", "劇本", "為什麼每次", "說好的一樣", "不是真的",
            "設計好", "騙我", "都知道", "重複", "一模一樣",
        ]
    )

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(
                f"未知的 provider {self.provider!r}，可選：{sorted(PROVIDERS)}"
            )
        if not self.models:
            self.models = provider_models(self.provider)

    def cache_min(self, model: str) -> int:
        return cache_min(self.provider, model)

    def price(self, model: str) -> tuple[float, float, float]:
        return price(self.provider, model)
