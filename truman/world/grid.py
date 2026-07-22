"""格子地圖：地形、區域、可視性、BFS 尋路。

世界引擎是權威狀態的唯一持有者。agent 只提交 intent，由這裡驗證。
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Pos:
    x: int
    y: int

    def as_list(self) -> list[int]:
        return [self.x, self.y]

    @staticmethod
    def of(seq) -> "Pos":
        return Pos(int(seq[0]), int(seq[1]))

    def chebyshev(self, other: "Pos") -> int:
        return max(abs(self.x - other.x), abs(self.y - other.y))

    def __str__(self) -> str:  # pragma: no cover - 顯示用
        return f"({self.x},{self.y})"


@dataclass
class Area:
    name: str
    x0: int
    y0: int
    x1: int
    y1: int
    description: str
    public: bool = True

    def contains(self, p: Pos) -> bool:
        return self.x0 <= p.x <= self.x1 and self.y0 <= p.y <= self.y1

    def center(self) -> Pos:
        return Pos((self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2)


class Grid:
    """不可變的地圖。所有世界狀態的空間查詢都走這裡。"""

    STREET = "街道"

    def __init__(self, rows: list[str], legend: dict[str, tuple[str, bool]], areas: list[Area]):
        self.rows = rows
        self.legend = legend
        self.h = len(rows)
        self.w = len(rows[0])
        for i, r in enumerate(rows):
            if len(r) != self.w:
                raise ValueError(f"地圖第 {i} 列寬度 {len(r)} != {self.w}")
        self.areas = {a.name: a for a in areas}

    # ------------------------------------------------------------ 地形
    def in_bounds(self, p: Pos) -> bool:
        return 0 <= p.x < self.w and 0 <= p.y < self.h

    def symbol(self, p: Pos) -> str:
        return self.rows[p.y][p.x]

    def terrain(self, p: Pos) -> str:
        return self.legend[self.symbol(p)][0]

    def walkable(self, p: Pos) -> bool:
        return self.in_bounds(p) and self.legend[self.symbol(p)][1]

    # ------------------------------------------------------------ 區域
    def area_at(self, p: Pos) -> str:
        for a in self.areas.values():
            if a.contains(p):
                return a.name
        return self.STREET

    def area(self, name: str) -> Area | None:
        return self.areas.get(name)

    def resolve_area(self, raw: str) -> str | None:
        """容錯的區域名解析 —— LLM 有時會回傳近似名稱。"""
        if not raw:
            return None
        raw = raw.strip()
        if raw in self.areas:
            return raw
        if raw == self.STREET:
            return self.STREET
        for name in self.areas:
            if name in raw or raw in name:
                return name
        return None

    def random_walkable_in(self, area_name: str, rng: random.Random) -> Pos:
        a = self.areas.get(area_name)
        if a is None:
            candidates = [
                Pos(x, y)
                for y in range(self.h)
                for x in range(self.w)
                if self.walkable(Pos(x, y)) and self.area_at(Pos(x, y)) == self.STREET
            ]
        else:
            candidates = [
                Pos(x, y)
                for y in range(a.y0, a.y1 + 1)
                for x in range(a.x0, a.x1 + 1)
                if self.walkable(Pos(x, y))
            ]
        if not candidates:
            raise ValueError(f"區域 {area_name} 沒有可站立的格子")
        return rng.choice(candidates)

    # ------------------------------------------------------------ 尋路
    def path(self, start: Pos, goal_area: str) -> list[Pos]:
        """BFS 到目標區域的最近可站立格。回傳不含起點的路徑。"""
        area = self.areas.get(goal_area)

        def is_goal(p: Pos) -> bool:
            if area is None:
                return self.area_at(p) == goal_area
            return area.contains(p)

        if is_goal(start):
            return []

        prev: dict[Pos, Pos | None] = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = Pos(cur.x + d[0], cur.y + d[1])
                if nxt in prev or not self.walkable(nxt):
                    continue
                prev[nxt] = cur
                if is_goal(nxt):
                    out = []
                    node: Pos | None = nxt
                    while node is not None and node != start:
                        out.append(node)
                        node = prev[node]
                    out.reverse()
                    return out
                q.append(nxt)
        return []  # 不可達

    # ------------------------------------------------------------ 渲染
    def render(self, occupants: dict[Pos, str] | None = None) -> str:
        occupants = occupants or {}
        out = []
        for y in range(self.h):
            line = []
            for x in range(self.w):
                p = Pos(x, y)
                line.append(occupants.get(p, self.rows[y][x]))
            out.append("".join(line))
        return "\n".join(out)

    def brief(self) -> str:
        """給 LLM 看的地圖說明（進快取區塊，必須完全穩定）。"""
        lines = [f"地圖尺寸：寬 {self.w} × 高 {self.h}，座標 (x,y)，左上角為 (0,0)。", ""]
        lines.append("```")
        lines.append(self.rows and self.render() or "")
        lines.append("```")
        lines.append("")
        lines.append("圖例：")
        for sym, (name, walk) in self.legend.items():
            lines.append(f"  {sym} = {name}（{'可通行' if walk else '不可通行'}）")
        lines.append("")
        lines.append("區域：")
        for a in self.areas.values():
            lines.append(
                f"  ● {a.name}  範圍 ({a.x0},{a.y0})–({a.x1},{a.y1})：{a.description}"
            )
        lines.append(f"  ● {self.STREET}：不屬於任何場所的戶外通道，人們在此往來、擦身而過。")
        return "\n".join(lines)
