"""A disk-backed section playlist; playback and synthesis advance independently."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SectionQueue:
    parts: list[Path] = field(default_factory=list)
    cursor: int = 0
    active: bool = True
    complete: bool = False

    def next(self) -> Path | None:
        if not self.active or self.cursor >= len(self.parts):
            return None
        part = self.parts[self.cursor]
        self.cursor += 1
        return part

    def restart(self):
        self.cursor = 0
        self.active = True
