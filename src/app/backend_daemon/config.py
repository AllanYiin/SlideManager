from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Aspect = Literal["4:3", "16:9", "unknown"]


class ThumbConfig(BaseModel):
    enabled: bool = True
    width: int = 320
    height_4_3: int = 240
    height_16_9: int = 180
    render_dpi: int = 144


class EmbedConfig(BaseModel):
    enabled_text: bool = True
    enabled_image: bool = True
    model_text: str = "text-embedding-3-large"
    model_image: str = "image-embedding-1"
    max_concurrency: int = 2
    batch_size: int = 64
    req_per_min: int = 120
    tok_per_min: int = 200000
    max_retries: int = 8


class PdfConfig(BaseModel):
    enabled: bool = True
    timeout_sec: int = 180
    max_concurrency: int = 1
    prefer: Literal["libreoffice", "powerpoint", "auto"] = "auto"


class JobOptions(BaseModel):
    enable_text: bool = True
    enable_thumb: bool = True
    enable_text_vec: bool = True
    enable_img_vec: bool = True
    enable_bm25: bool = True
    file_paths: list[str] = Field(default_factory=list)
    thumb: ThumbConfig = Field(default_factory=ThumbConfig)
    pdf: PdfConfig = Field(default_factory=PdfConfig)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    commit_every_pages: int = 50
    commit_every_sec: float = 1.0
    enable_sentence_df: bool = True
    sentence_df_threshold: float = 0.30
    sentence_min_len: int = 6
