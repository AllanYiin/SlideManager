from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.backend_daemon.utils_win import is_windows, kill_process_tree_windows


def _file_url(path: Path) -> str:
    p = path.resolve().as_posix()
    if not p.startswith("/"):
        return "file:///" + p
    return "file://" + p


def convert_pptx_to_pdf_libreoffice(
    pptx_path: Path, out_pdf: Path, soffice_path: Optional[str], timeout_sec: int
) -> None:
    if soffice_path is None:
        soffice_path = "soffice"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lo_profile_") as prof_dir:
        prof_path = Path(prof_dir)
        user_install = f"-env:UserInstallation={_file_url(prof_path)}"

        cmd = [
            soffice_path,
            "--headless",
            "--nologo",
            "--norestore",
            "--nofirststartwizard",
            user_install,
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_pdf.parent),
            str(pptx_path),
        ]

        creationflags = 0
        if is_windows():
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags,
        )
        try:
            _stdout, stderr = proc.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired as exc:
            if is_windows():
                kill_process_tree_windows(proc.pid)
            else:
                proc.kill()
            raise RuntimeError(
                f"LibreOffice timeout after {timeout_sec}s: {pptx_path}"
            ) from exc

        if proc.returncode != 0:
            raise RuntimeError(f"LibreOffice failed rc={proc.returncode}: {stderr[:500]}")

        expected = out_pdf.parent / (pptx_path.stem + ".pdf")
        if not expected.exists():
            raise RuntimeError(f"PDF not produced: expected {expected}")

        if expected.resolve() != out_pdf.resolve():
            if out_pdf.exists():
                out_pdf.unlink()
            expected.replace(out_pdf)
