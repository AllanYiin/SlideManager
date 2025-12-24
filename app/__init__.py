from __future__ import annotations

import sys
from pathlib import Path
from pkgutil import extend_path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"

if SRC_DIR.is_dir():
    src_path = str(SRC_DIR)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

__path__ = extend_path(__path__, __name__)
