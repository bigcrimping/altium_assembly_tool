"""population_state.py — Tracks which component designators have been placed."""
from __future__ import annotations

import json
from pathlib import Path


class PopulationState:
    def __init__(self) -> None:
        self._placed: set[str] = set()
        self.is_modified: bool = False

    @property
    def placed(self) -> frozenset[str]:
        return frozenset(self._placed)

    def is_placed(self, designator: str) -> bool:
        return designator in self._placed

    def toggle(self, designator: str) -> bool:
        """Toggle placed state. Returns True if now placed."""
        if designator in self._placed:
            self._placed.discard(designator)
        else:
            self._placed.add(designator)
        self.is_modified = True
        return designator in self._placed

    def clear(self) -> None:
        self._placed.clear()
        self.is_modified = False

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"placed": sorted(self._placed)}, indent=2), encoding="utf-8")
        self.is_modified = False

    def load(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self._placed = set(data.get("placed", []))
        self.is_modified = False
