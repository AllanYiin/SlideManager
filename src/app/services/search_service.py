# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.services.project_store import ProjectStore

log = get_logger(__name__)


@dataclass
class SearchQuery:
    text: str
    mode: str = "hybrid"
    weight_text: float = 0.5
    weight_vector: float = 0.5
    top_k: int = 50


@dataclass
class SearchResult:
    slide: Dict[str, Any]
    score: float
    bm25: float
    vec: float


class SearchService:
    def __init__(self, store: ProjectStore, api_key: Optional[str]):
        self.store = store
        self.api_key = api_key

    def search(self, q: SearchQuery, image_vec: Optional[object] = None) -> List[SearchResult]:
        log.warning("搜尋功能已移至後台 daemon，目前 UI 尚未接線。")
        return []
