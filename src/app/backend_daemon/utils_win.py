from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

import shutil


def is_windows() -> bool:
    return sys.platform.startswith("win")


def kill_process_tree_windows(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )


def which_soffice_windows() -> Optional[str]:
    for name in ("soffice.exe",):
        p = shutil.which(name)
        if p:
            return p
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None
