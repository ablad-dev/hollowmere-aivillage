"""Village geography: collision grid, locations, and pathfinding.

Agents never emit raw coordinates. They choose a *named location*, and the
world resolves that into a walkable path. This keeps the LLM out of the
business of navigation, where it is reliably terrible.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .config import VILLAGE_PATH


@dataclass
class Location:
    name: str
    x: int
    y: int
    kinds: list[str]
    desc: str = ""
    job: str | None = None
    price: int = 0
    area: str = "Outdoors"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "kinds": self.kinds,
            "desc": self.desc,
            "job": self.job,
            "price": self.price,
            "area": self.area,
        }


class World:
    def __init__(self, path=VILLAGE_PATH):
        self.raw = json.loads(path.read_text(encoding="utf-8"))
        self.name: str = self.raw["name"]
        self.width: int = self.raw["width"]
        self.height: int = self.raw["height"]
        self.tile_size: int = self.raw["tile_size"]
        self.blocked: set[tuple[int, int]] = set()
        self.locations: dict[str, Location] = {}
        self._path_cache: dict[tuple, list[tuple[int, int]]] = {}
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        for b in self.raw["buildings"]:
            x0, y0 = b["x"], b["y"]
            x1, y1 = x0 + b["w"] - 1, y0 + b["h"] - 1
            door = tuple(b["door"])
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    on_edge = x in (x0, x1) or y in (y0, y1)
                    if on_edge and (x, y) != door:
                        self.blocked.add((x, y))

        for p in self.raw.get("props", []):
            self.blocked.add((p["x"], p["y"]))

        for loc in self.raw["locations"]:
            location = Location(
                name=loc["name"],
                x=loc["x"],
                y=loc["y"],
                kinds=loc.get("kinds", []),
                desc=loc.get("desc", ""),
                job=loc.get("job"),
                price=loc.get("price", 0),
            )
            location.area = self.area_at(location.x, location.y)
            self.locations[location.name] = location
            # A location tile must be standable.
            self.blocked.discard((location.x, location.y))

    # ------------------------------------------------------------------
    def area_at(self, x: int, y: int) -> str:
        for b in self.raw["buildings"]:
            if b["x"] <= x < b["x"] + b["w"] and b["y"] <= y < b["y"] + b["h"]:
                return b["name"]
        for z in self.raw.get("zones", []):
            if z["x"] <= x < z["x"] + z["w"] and z["y"] <= y < z["y"] + z["h"]:
                return z["name"]
        return "Outdoors"

    def walkable(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height and (x, y) not in self.blocked

    def nearest_walkable(self, x: int, y: int) -> tuple[int, int]:
        if self.walkable(x, y):
            return x, y
        for r in range(1, 8):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if self.walkable(x + dx, y + dy):
                        return x + dx, y + dy
        return 0, 0

    # ------------------------------------------------------------------
    def path(self, start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
        """BFS on the 4-connected grid. The map is tiny; this is plenty fast."""
        start = self.nearest_walkable(*start)
        goal = self.nearest_walkable(*goal)
        if start == goal:
            return []
        key = (start, goal)
        if key in self._path_cache:
            return list(self._path_cache[key])

        came: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            if cur == goal:
                break
            cx, cy = cur
            for nxt in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if nxt not in came and self.walkable(*nxt):
                    came[nxt] = cur
                    queue.append(nxt)

        if goal not in came:
            return []
        route: list[tuple[int, int]] = []
        node: tuple[int, int] | None = goal
        while node and node != start:
            route.append(node)
            node = came[node]
        route.reverse()
        self._path_cache[key] = list(route)
        return route

    # ------------------------------------------------------------------
    def by_kind(self, kind: str) -> list[Location]:
        return [l for l in self.locations.values() if kind in l.kinds]

    def resolve(self, name: str | None) -> Location | None:
        """Tolerant lookup - models rarely echo a name byte-for-byte."""
        if not name:
            return None
        name = name.strip()
        if name in self.locations:
            return self.locations[name]
        lowered = name.lower()
        for key, loc in self.locations.items():
            if key.lower() == lowered:
                return loc
        for key, loc in self.locations.items():
            if lowered in key.lower() or key.lower() in lowered:
                return loc
        for key, loc in self.locations.items():
            if lowered in loc.area.lower() or loc.area.lower() in lowered:
                return loc
        return None

    def catalogue(self, kinds: Iterable[str] | None = None) -> str:
        out = []
        for loc in self.locations.values():
            if kinds and not set(kinds) & set(loc.kinds):
                continue
            tags = "/".join(loc.kinds)
            out.append(f"- {loc.name} ({tags}, in {loc.area}): {loc.desc}")
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "tile_size": self.tile_size,
            "buildings": self.raw["buildings"],
            "zones": self.raw.get("zones", []),
            "props": self.raw.get("props", []),
            "locations": [l.to_dict() for l in self.locations.values()],
            "blocked": sorted([list(t) for t in self.blocked]),
        }


world = World()
