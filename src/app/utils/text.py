# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from typing import List


_WORD_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> List[str]:
    text = (text or "").strip().lower()
    if not text:
        return []
    toks: List[str] = []
    for m in _WORD_RE.finditer(text):
        s = m.group(0)
        if not s:
            continue
        # CJK 長字串：拆成字 + 2-gram，提高召回
        if re.fullmatch(r"[\u4e00-\u9fff]+", s) and len(s) > 1:
            toks.extend(list(s))
            toks.extend([s[i : i + 2] for i in range(len(s) - 1)])
        else:
            toks.append(s)
    return toks
