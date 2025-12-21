# -*- coding: utf-8 -*-

from __future__ import annotations

from array import array
from math import sqrt
from typing import Iterable, List, Sequence

float32 = "float32"


class ndarray:
    def __init__(self, data: Iterable[float]):
        self._data = array("f", data)

    @property
    def size(self) -> int:
        return len(self._data)

    def reshape(self, *_shape: int) -> "ndarray":
        return self

    def tobytes(self) -> bytes:
        return self._data.tobytes()

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return ndarray(self._data[item])
        return self._data[item]

    def __truediv__(self, other: float) -> "ndarray":
        return ndarray([v / other for v in self._data])

    def __repr__(self) -> str:
        return f"ndarray({list(self._data)!r})"


def asarray(data: Iterable[float], dtype: str | None = None) -> ndarray:
    if isinstance(data, ndarray):
        return data
    return ndarray(data)


def frombuffer(buf: bytes, dtype: str | None = None) -> ndarray:
    values = array("f")
    values.frombytes(buf)
    return ndarray(values)


def zeros(shape: Sequence[int], dtype: str | None = None) -> ndarray:
    if len(shape) != 1:
        raise ValueError("Only 1D zeros are supported in the lightweight numpy shim.")
    return ndarray([0.0 for _ in range(shape[0])])


def concatenate(arrays: Sequence[ndarray], axis: int = 0) -> ndarray:
    if axis != 0:
        raise ValueError("Only axis=0 concatenation is supported in the lightweight numpy shim.")
    merged: List[float] = []
    for arr in arrays:
        merged.extend(list(arr))
    return ndarray(merged)


def dot(a: Iterable[float], b: Iterable[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def allclose(a: Iterable[float], b: Iterable[float], rtol: float = 1e-05, atol: float = 1e-08) -> bool:
    a_list = list(a)
    b_list = list(b)
    if len(a_list) != len(b_list):
        return False
    return all(abs(x - y) <= (atol + rtol * abs(y)) for x, y in zip(a_list, b_list))


def arange(n: int, dtype: str | None = None) -> ndarray:
    return ndarray([float(i) for i in range(n)])


class _LinalgModule:
    @staticmethod
    def norm(vec: Iterable[float]) -> float:
        return sqrt(sum(v * v for v in vec))


linalg = _LinalgModule()

