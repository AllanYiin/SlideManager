from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple


@dataclass
class Image:
    size: Tuple[int, int]

    def save(self, path: str | Path) -> None:
        Path(path).write_text(f"FAKEIMAGE {self.size[0]} {self.size[1]}", encoding="utf-8")

    def __enter__(self) -> "Image":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


def new(_mode: str, size: Iterable[int], _color: object = None, **_kwargs: object) -> Image:
    width, height = size
    return Image((int(width), int(height)))


def open(path: str | Path) -> Image:
    content = Path(path).read_text(encoding="utf-8")
    _, width, height = content.split()
    return Image((int(width), int(height)))
