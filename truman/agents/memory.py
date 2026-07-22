"""Memory stream —— 抄 Generative Agents (Park et al., UIST 2023) 的三要素檢索。

score = w_r · recency + w_i · importance + w_v · relevance

relevance 目前用詞彙重疊（zh 用 bigram，en 用 token）。這是刻意的取捨：
Phase 1 不引入 torch / sentence-transformers。Retriever 介面留著，
之後要換成 embedding 檢索只需替換 `relevance()`。
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿]")


def _shingles(text: str) -> set[str]:
    """把中英混排文字切成可比對的片段集合。"""
    out: set[str] = set(m.group(0).lower() for m in _TOKEN_RE.finditer(text))
    cjk = "".join(ch for ch in text if _CJK_RE.match(ch))
    out.update(cjk[i : i + 2] for i in range(len(cjk) - 1))
    out.update(cjk)  # 單字也算，短查詢才不會空手而回
    return out


@dataclass
class MemoryEntry:
    id: int
    tick: int
    when: str  # 人類可讀的模擬時間
    kind: str  # observation | speech | thought | reflection | injected
    content: str
    importance: int  # 1–10
    last_access_tick: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "MemoryEntry":
        return MemoryEntry(**d)


@dataclass
class MemoryStream:
    entries: list[MemoryEntry] = field(default_factory=list)
    next_id: int = 1
    importance_since_reflection: int = 0
    beliefs: list[str] = field(default_factory=list)  # reflection 產出的穩定信念
    last_reflection_tick: int = -1

    # ------------------------------------------------------------ 寫入
    def add(self, tick: int, when: str, kind: str, content: str, importance: int) -> MemoryEntry:
        e = MemoryEntry(
            id=self.next_id,
            tick=tick,
            when=when,
            kind=kind,
            content=content,
            importance=max(1, min(10, int(importance))),
            last_access_tick=tick,
        )
        self.entries.append(e)
        self.next_id += 1
        self.importance_since_reflection += e.importance
        return e

    def prune(self, cap: int) -> None:
        """超過上限時，丟掉最不重要且最舊的 episodic 記憶（reflection 永不丟）。"""
        if len(self.entries) <= cap:
            return
        keep_always = [e for e in self.entries if e.kind == "reflection"]
        rest = [e for e in self.entries if e.kind != "reflection"]
        rest.sort(key=lambda e: (e.importance, e.tick))
        drop = len(self.entries) - cap
        rest = rest[drop:]
        merged = keep_always + rest
        merged.sort(key=lambda e: e.id)
        self.entries = merged

    # ------------------------------------------------------------ 檢索
    @staticmethod
    def relevance(query: str, content: str) -> float:
        q, c = _shingles(query), _shingles(content)
        if not q or not c:
            return 0.0
        return len(q & c) / math.sqrt(len(q) * len(c))

    def retrieve(self, query: str, tick: int, k: int, cfg) -> list[MemoryEntry]:
        if not self.entries:
            return []
        scored = []
        for e in self.entries:
            age = max(0, tick - e.last_access_tick)
            recency = 0.5 ** (age / cfg.recency_halflife)
            importance = e.importance / 10.0
            rel = self.relevance(query, e.content)
            score = (
                cfg.w_recency * recency
                + cfg.w_importance * importance
                + cfg.w_relevance * rel
            )
            scored.append((score, e))
        scored.sort(key=lambda t: (-t[0], -t[1].tick))
        picked = [e for _, e in scored[:k]]
        for e in picked:
            e.last_access_tick = tick  # 被想起就重置近時性
        picked.sort(key=lambda e: e.tick)
        return picked

    def recent(self, n: int) -> list[MemoryEntry]:
        return self.entries[-n:]

    # ------------------------------------------------------------ 信念
    def set_beliefs(self, beliefs: list[str], cap: int) -> None:
        """reflection 產出的高階信念。這段會進 prompt 的快取區塊，
        所以只在 reflection 時變動 —— 每次變動會付一次 cache write。"""
        self.beliefs = beliefs[:cap]

    def beliefs_block(self) -> str:
        if not self.beliefs:
            return "（尚無沉澱下來的判斷。）"
        return "\n".join(f"- {b}" for b in self.beliefs)

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "next_id": self.next_id,
            "importance_since_reflection": self.importance_since_reflection,
            "beliefs": self.beliefs,
            "last_reflection_tick": self.last_reflection_tick,
        }

    @staticmethod
    def from_dict(d: dict) -> "MemoryStream":
        return MemoryStream(
            entries=[MemoryEntry.from_dict(x) for x in d.get("entries", [])],
            next_id=d.get("next_id", 1),
            importance_since_reflection=d.get("importance_since_reflection", 0),
            beliefs=list(d.get("beliefs", [])),
            last_reflection_tick=d.get("last_reflection_tick", -1),
        )
