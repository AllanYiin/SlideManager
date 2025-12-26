#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ============================================================
# 全自動設定
# ============================================================

DEFAULT_VENV_DIR = ".venv"
EXCLUDE_DIRS = {".venv", "venv", "__pycache__", ".git", ".idea", ".vscode", "dist", "build", "node_modules"}

MODULE_ENTRY_CANDIDATES = [
    "src/main.py",
    "src/app.py",
    "backend/main.py",
    "backend/app.py",
    "backend/run_server.py",
    "backend/__main__.py",
    "backend/app/__main__.py",
    "backend/app/main.py",
]

KNOWN_PACKAGE_ROOTS = ("src", "backend", "app")

LOCAL_NAME_BLOCKLIST = {
    "app", "apps",
    "db", "database",
    "config", "configs", "settings",
    "utils", "common", "core",
    "src", "backend", "frontend",
    "main", "server", "run_server",
    "models", "schemas", "routers", "routes",
    "tests", "test",
}

IMPORT_TO_PIP_MAP = {
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "Crypto": "pycryptodome",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "pydantic_settings": "pydantic-settings",
}

FRONTEND_PKG_CANDIDATES = [
    "frontend/package.json",
    "client/package.json",
    "web/package.json",
    "ui/package.json",
    "package.json",
]

STATIC_SITE_DIR_CANDIDATES = [
    "dist",
    "build",
    "public",
    "frontend/public",
    "frontend/dist",
    "frontend/build",
    "web/dist",
    "web/build",
    "client/dist",
    "client/build",
    "ui/dist",
    "ui/build",
]

# ============================================================
# Utilities
# ============================================================

def is_windows() -> bool:
    return os.name == "nt"

def norm_rel(root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(root))
    except Exception:
        return str(p)

def run_cmd(cmd: List[str], cwd: Optional[Path] = None) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            check=False,
        )
        return p.returncode, p.stdout
    except FileNotFoundError as e:
        return 127, f"Command not found: {cmd[0]} ({e})"
    except Exception as e:
        return 1, f"Command failed: {' '.join(cmd)} ({e})"

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")

def write_text_utf8_bom(p: Path, text: str) -> None:
    # Use UTF-8 with BOM for maximum compatibility with Windows cmd.exe/bat files.
    # newline is forced to CRLF to avoid edge cases in some Windows environments.
    p.write_text(text, encoding="utf-8-sig", newline="\r\n")

def safe_int(s: str) -> Optional[int]:
    try:
        v = int(s)
        return v if 1 <= v <= 65535 else None
    except Exception:
        return None

def resolve_backend_host_port(cfg: Dict[str, str],
                             fallback_host: Optional[str],
                             fallback_port: Optional[int]) -> Tuple[str, int]:
    """Resolve backend host/port with convention:
    BACKEND_* > APP_* > PORT (port only) > detected fallback > defaults.
    """
    host = (cfg.get("BACKEND_HOST") or cfg.get("APP_HOST") or fallback_host or "127.0.0.1").strip()
    port = (safe_int(cfg.get("BACKEND_PORT", "")) or
            safe_int(cfg.get("APP_PORT", "")) or
            safe_int(cfg.get("PORT", "")) or
            fallback_port or
            8000)
    return host, port


# ============================================================
# Optional config: .launcher.env (no extra CLI options)
# ============================================================

def parse_env_file(env_path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def ensure_dotenv_from_example(root: Path) -> None:
    """If .env is missing but .env.example exists, copy it to .env (UTF-8)."""
    env_path = root / ".env"
    example_path = root / ".env.example"
    if not env_path.exists() and example_path.exists():
        env_path.write_text(example_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        print("[FIX] 已由 .env.example 建立 .env")

def get_launcher_config(root: Path) -> Dict[str, str]:
    # precedence: OS env > .launcher.env > .env
    env_cfg = parse_env_file(root / ".env")
    launcher_cfg = parse_env_file(root / ".launcher.env")
    cfg: Dict[str, str] = {}
    cfg.update(env_cfg)
    cfg.update(launcher_cfg)
    for k, v in os.environ.items():
        if k in {
            "BACKEND_HOST", "BACKEND_PORT", "UVICORN_TARGET", "BACKEND_START", "APP_START",
            "FRONTEND_HOST", "FRONTEND_PORT", "APP_HOST", "APP_PORT",
            "STATIC_HOST", "STATIC_PORT",
            "PORT",
        }:
            cfg[k] = v
    # compatibility: APP_HOST/APP_PORT <-> BACKEND_HOST/BACKEND_PORT
    if not cfg.get("BACKEND_HOST") and cfg.get("APP_HOST"):
        cfg["BACKEND_HOST"] = cfg["APP_HOST"]
    if not cfg.get("BACKEND_PORT") and cfg.get("APP_PORT"):
        cfg["BACKEND_PORT"] = cfg["APP_PORT"]
    if cfg.get("BACKEND_HOST") and not cfg.get("APP_HOST"):
        cfg["APP_HOST"] = cfg["BACKEND_HOST"]
    if cfg.get("BACKEND_PORT") and not cfg.get("APP_PORT"):
        cfg["APP_PORT"] = cfg["BACKEND_PORT"]
    # compatibility: PORT -> BACKEND_PORT/APP_PORT (common platform convention)
    if not cfg.get("BACKEND_PORT") and cfg.get("PORT"):
        cfg["BACKEND_PORT"] = cfg["PORT"]
    if not cfg.get("APP_PORT") and cfg.get("PORT"):
        cfg["APP_PORT"] = cfg["PORT"]

    return cfg

# ============================================================
# Requirements: parse + auto-generate/fix
# ============================================================

REQ_LINE_RE = re.compile(
    r"""^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<spec>(==|>=|<=|~=|!=|>|<).+)?\s*$""",
    re.VERBOSE,
)

@dataclass
class RequirementsInfo:
    packages: Set[str] = field(default_factory=set)
    directive_lines: List[str] = field(default_factory=list)

def parse_requirements(req_path: Path) -> RequirementsInfo:
    info = RequirementsInfo()
    if not req_path.exists():
        return info
    for line in req_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(("-", "--")) or "://" in s or s.startswith("git+"):
            info.directive_lines.append(line)
            continue
        m = REQ_LINE_RE.match(s)
        if not m:
            info.directive_lines.append(line)
            continue
        info.packages.add(m.group("name").strip().lower())
    return info

def stdlib_names() -> Set[str]:
    names = getattr(sys, "stdlib_module_names", None)
    return set(names) if names else set()

def detect_local_toplevel(root: Path) -> Set[str]:
    local: Set[str] = set()
    for child in root.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_file() and child.suffix == ".py":
            local.add(child.stem.lower())
        if child.is_dir() and (child / "__init__.py").exists():
            local.add(child.name.lower())

    src_dir = root / "src"
    if src_dir.is_dir():
        for c in src_dir.iterdir():
            if c.name.startswith("."):
                continue
            if c.is_file() and c.suffix == ".py":
                local.add(c.stem.lower())
            if c.is_dir() and (c / "__init__.py").exists():
                local.add(c.name.lower())

    return local

def normalize_to_pip_name(mod: str) -> str:
    return IMPORT_TO_PIP_MAP.get(mod, mod).lower().replace("_", "-")

def filter_third_party_candidates(root: Path, imported_modules: Set[str]) -> List[str]:
    stdlib = stdlib_names()
    local = detect_local_toplevel(root)
    out: Set[str] = set()
    for m in imported_modules:
        ml = m.lower()
        if ml in stdlib or ml in local or ml in LOCAL_NAME_BLOCKLIST or ml in {"__future__", "builtins"}:
            continue
        out.add(normalize_to_pip_name(m))
    return sorted(out)

def generate_or_fix_requirements(root: Path, pkgs: List[str]) -> None:
    req_path = root / "requirements.txt"
    header = [
        "# Auto-generated requirements.txt",
        "# Generated by project_launcher.py",
        "# Note: versions are intentionally not pinned. Pin after first successful install if needed.",
        "",
    ]
    req_path.write_text("\n".join(header + sorted(set(pkgs))) + "\n", encoding="utf-8")
    print("[FIX] requirements.txt 已自動建立/修正（已排除 stdlib/本地模組/常見黑名單）。")

# ============================================================
# Scan imports (AST)
# ============================================================

@dataclass
class ImportUsage:
    file: Path
    module: str
    lineno: int

@dataclass
class ScanResult:
    imports: List[ImportUsage] = field(default_factory=list)
    syntax_errors: List[Tuple[Path, str]] = field(default_factory=list)

class ImportScanner(ast.NodeVisitor):
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.imports: List[ImportUsage] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name.split(".")[0]
            self.imports.append(ImportUsage(self.file_path, mod, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if getattr(node, "level", 0) and node.level > 0:
            return
        if node.module:
            mod = node.module.split(".")[0]
            self.imports.append(ImportUsage(self.file_path, mod, node.lineno))
        self.generic_visit(node)

def scan_imports(root: Path) -> ScanResult:
    res = ScanResult()
    for p in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        try:
            code = p.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(code, filename=str(p))
            sc = ImportScanner(p)
            sc.visit(tree)
            res.imports.extend(sc.imports)
        except SyntaxError as e:
            res.syntax_errors.append((p, f"{e.msg} (line {e.lineno})"))
        except Exception as e:
            res.syntax_errors.append((p, f"Parse error: {e}"))
    return res

# ============================================================
# venv + install + pip check + import test
# ============================================================

def venv_python(root: Path, venv_dir: str) -> Path:
    venv_path = (root / venv_dir).resolve()
    return venv_path / ("Scripts/python.exe" if is_windows() else "bin/python")

def ensure_venv(root: Path, venv_dir: str) -> None:
    vp = venv_python(root, venv_dir)
    if vp.exists():
        return
    rc, out = run_cmd([sys.executable, "-m", "venv", str((root / venv_dir).resolve())], cwd=root)
    print(out.rstrip())
    if rc != 0:
        raise RuntimeError("無法建立虛擬環境。可能權限不足或被防毒攔截。")

def pip_install_requirements(root: Path, venv_dir: str) -> None:
    vp = venv_python(root, venv_dir)
    req = root / "requirements.txt"
    if not req.exists():
        raise RuntimeError("找不到 requirements.txt，無法安裝套件。")

    rc, out = run_cmd([str(vp), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
    print(out.rstrip())
    rc2, out2 = run_cmd([str(vp), "-m", "pip", "install", "-r", str(req)], cwd=root)
    print(out2.rstrip())
    if rc2 != 0:
        raise RuntimeError("pip install -r requirements.txt 失敗。可能是套件名錯誤、網路/代理、或版本衝突。")

def pip_check(root: Path, venv_dir: str) -> None:
    vp = venv_python(root, venv_dir)
    rc, out = run_cmd([str(vp), "-m", "pip", "check"], cwd=root)
    print(out.rstrip())
    if rc != 0:
        raise RuntimeError("pip check 發現依賴問題（缺依賴或衝突）。")

def import_test_third_party(root: Path, venv_dir: str, imported_modules: Set[str]) -> None:
    vp = venv_python(root, venv_dir)
    stdlib = stdlib_names()
    local = detect_local_toplevel(root)

    failed: List[str] = []
    for mod in sorted(imported_modules):
        ml = mod.lower()
        if ml in stdlib or ml in local or ml in LOCAL_NAME_BLOCKLIST or ml in {"__future__", "builtins"}:
            continue
        rc, _ = run_cmd([str(vp), "-c", f"import {mod}"], cwd=root)
        if rc != 0:
            failed.append(mod)

    if failed:
        msg = "以下模組無法 import（通常代表 requirements.txt 缺漏、套件名對不到、或版本不相容）：\n" + "\n".join(f"- {m}" for m in failed)
        raise RuntimeError(msg)

# ============================================================
# Backend detection: uvicorn target + module fallback (no hardcode)
# ============================================================

STREAMLIT_PAT = re.compile(r"(?m)^\s*(import\s+streamlit\s+as\s+st|from\s+streamlit\s+import\s+)")

def file_contains(path: Path, pattern: re.Pattern) -> bool:
    try:
        return pattern.search(read_text(path)) is not None
    except Exception:
        return False

# uvicorn xxx:yyy [--host ...] [--port ...]
UVI_CMD_RE = re.compile(
    r"""(?ix)
    (?:python\s+-m\s+uvicorn|uvicorn)\s+
    (?P<target>[A-Za-z0-9_\.]+:[A-Za-z0-9_]+)
    (?P<rest>.*)
    """
)

def parse_host_port_from_args(text: str) -> Tuple[Optional[str], Optional[int]]:
    host = None
    port = None

    m = re.search(r"""(?ix)\s--host(?:\s+|=)(?P<host>[A-Za-z0-9\.\-_:]+)""", text)
    if m:
        host = m.group("host").strip()

    m = re.search(r"""(?ix)\s--port(?:\s+|=)(?P<port>\d{{2,5}})""", text)
    if m:
        port = safe_int(m.group("port"))

    if port is None:
        m = re.search(r"""(?ix)\s-p(?:\s+|=)(?P<port>\d{{2,5}})""", text)
        if m:
            port = safe_int(m.group("port"))

    return host, port

def detect_uvicorn_from_text(text: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    m = UVI_CMD_RE.search(text)
    if m:
        target = m.group("target")
        rest = m.group("rest") or ""
        host, port = parse_host_port_from_args(rest)
        return target, host, port

    # argv-list 型（務實 window 掃描）
    idx = text.lower().find("uvicorn")
    if idx != -1:
        window = text[idx: idx + 400]
        mm = re.search(r"""(?ix)(?P<target>[A-Za-z0-9_\.]+:[A-Za-z0-9_]+)""", window)
        if mm:
            target = mm.group("target")
            host, port = parse_host_port_from_args(window)
            return target, host, port

    return None, None, None

def infer_uvicorn_target_from_code(root: Path) -> Optional[str]:
    # 保守推：FastAPI/ASGI app assignment
    FASTAPI_HINT_RE = re.compile(r"(?m)^\s*(from\s+fastapi\s+import\s+FastAPI|import\s+fastapi)\b|\bFastAPI\s*\(")
    ASGI_ASSIGN_RE = re.compile(r"(?m)^\s*(app|application)\s*=\s*")

    candidates: List[Tuple[str, Path]] = []
    for py in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        try:
            t = read_text(py)
        except Exception:
            continue
        if not (FASTAPI_HINT_RE.search(t) and ASGI_ASSIGN_RE.search(t)):
            continue

        rel = py.relative_to(root)
        parts = list(rel.parts)
        parts[-1] = Path(parts[-1]).stem
        mod = ".".join(parts)

        if re.search(r"(?m)^\s*app\s*=", t):
            candidates.append((f"{mod}:app", py))
        if re.search(r"(?m)^\s*application\s*=", t):
            candidates.append((f"{mod}:application", py))

    if not candidates:
        return None

    uniq: Dict[str, Path] = {t: f for t, f in candidates}
    items = list(uniq.items())

    def score(item: Tuple[str, Path]) -> Tuple[int, int]:
        target, f = item
        s1 = len(f.parts)
        s2 = 0
        name = f.stem.lower()
        if name in ("main", "app"):
            s2 -= 2
        if "backend" in [x.lower() for x in f.parts]:
            s2 -= 1
        if "app" in [x.lower() for x in f.parts]:
            s2 -= 1
        return (s1, s2)

    items.sort(key=score)
    return items[0][0]

def parse_backend_start_override(cfg: Dict[str, str]) -> Optional[dict]:
    start_val = cfg.get("BACKEND_START") or cfg.get("APP_START")
    if not start_val:
        return None
    raw = start_val.strip()
    notes = [f"Detected BACKEND_START/APP_START override: {raw}"]

    target, host, port = detect_uvicorn_from_text(raw)
    if target:
        return {"mode": "uvicorn", "target": target, "host": host, "port": port, "notes": notes}

    if re.match(r"^[A-Za-z0-9_.]+:[A-Za-z0-9_]+$", raw):
        return {"mode": "uvicorn", "target": raw, "host": None, "port": None, "notes": notes}

    m = re.search(r"""(?i)python\s+-m\s+(?P<module>[A-Za-z0-9_\.]+)""", raw)
    if m:
        mod = m.group("module")
        return {"mode": "module", "module": mod, "notes": notes}

    if re.match(r"^[A-Za-z0-9_\.]+$", raw):
        return {"mode": "module", "module": raw, "notes": notes}

    return None

def detect_backend_mode(root: Path, cfg: Dict[str, str]) -> dict:
    """
    返回 dict:
      uvicorn: {mode, target, host, port, notes}
      module : {mode, module, file}
      streamlit fallback
    """
    notes: List[str] = []

    override = parse_backend_start_override(cfg)
    if override:
        override["notes"] = notes + override.get("notes", [])
        return override

    # 0) user-specified override (env/.launcher.env)
    if cfg.get("UVICORN_TARGET"):
        target = cfg["UVICORN_TARGET"].strip()
        host = cfg.get("BACKEND_HOST", "").strip() or None
        port = safe_int(cfg.get("BACKEND_PORT", "")) if cfg.get("BACKEND_PORT") else None
        notes.append("Using UVICORN_TARGET from config.")
        return {"mode": "uvicorn", "target": target, "host": host, "port": port, "notes": notes}

    # 0.5) Prefer project-standard backend launcher if present (root start_backend.py)
    if (root / "start_backend.py").is_file():
        notes.append("Using start_backend.py (project standard).")
        return {"mode": "script", "script": "start_backend.py", "notes": notes}


    # 1) command/argv-based detection
    for ext in (".bat", ".cmd", ".ps1", ".sh", ".yml", ".yaml", ".md", ".txt", ".py"):
        for p in root.rglob(f"*{ext}"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            try:
                t = read_text(p)
            except Exception:
                continue

            target, host, port = detect_uvicorn_from_text(t)
            if target:
                notes.append(f"Found uvicorn target in {p}: {target}")
                return {"mode": "uvicorn", "target": target, "host": host, "port": port, "notes": notes}

    # 2) infer from code
    inferred = infer_uvicorn_target_from_code(root)
    if inferred:
        notes.append("Inferred uvicorn target from code (FastAPI/ASGI assignment).")
        return {"mode": "uvicorn", "target": inferred, "host": None, "port": None, "notes": notes}

    # 3) streamlit fallback (only if truly streamlit)
    for rel in ["streamlit_app.py", "src/streamlit_app.py", "src/app.py", "src/main.py", "app.py", "main.py"]:
        p = root / rel
        if p.is_file() and file_contains(p, STREAMLIT_PAT):
            return {"mode": "streamlit", "file": str(p.relative_to(root)).replace("/", "\\")}

    # 4) module fallback (ensure backend still starts)
    for rel in MODULE_ENTRY_CANDIDATES:
        p = root / rel
        if p.is_file():
            relp = str(p.relative_to(root)).replace("/", "\\")
            mod = relp[:-3].replace("\\", ".")
            return {"mode": "module", "module": mod, "file": relp}

    return {"mode": "none", "notes": notes}

def needs_src_pythonpath_fix(root: Path, entry_module: str) -> bool:
    if not entry_module.startswith("src."):
        return False
    src_dir = root / "src"
    if not src_dir.is_dir():
        return False
    suspects = ["utils", "config", "db", "core", "common", "services", "routers", "models", "schemas"]
    for name in suspects:
        has_src = (src_dir / name).is_dir() or (src_dir / f"{name}.py").is_file()
        has_root = (root / name).is_dir() or (root / f"{name}.py").is_file()
        if has_src and not has_root:
            return True
    return False


def module_exists_in(base: Path, module: str) -> bool:
    mod_path = base.joinpath(*module.split("."))
    return mod_path.with_suffix(".py").is_file() or (mod_path / "__init__.py").is_file()


def needs_src_pythonpath_for_uvicorn(root: Path, target: str) -> bool:
    module = target.split(":", 1)[0]
    if not module:
        return False
    src_base = root / "src"
    if not src_base.is_dir():
        return False
    in_src = module_exists_in(src_base, module)
    in_root = module_exists_in(root, module)
    return in_src and not in_root


def detect_backend_worker_module(root: Path, backend: dict) -> Optional[str]:
    if backend.get("mode") != "uvicorn":
        return None
    target = backend.get("target", "")
    if "backend_daemon.main:app" not in target:
        return None
    for candidate in (
        root / "src" / "app" / "backend_daemon" / "worker.py",
        root / "app" / "backend_daemon" / "worker.py",
    ):
        if candidate.is_file():
            return "app.backend_daemon.worker"
    return None

# ============================================================
# Frontend detection + Static site detection
# ============================================================

@dataclass
class FrontendInfo:
    exists: bool = False
    dir: str = ""
    pm: str = ""
    script: str = ""
    install_cmd: str = ""
    run_cmd: str = ""
    host: Optional[str] = None
    port: Optional[int] = None
    mode: str = "node"  # node|static_serve
    static_dir: str = ""  # e.g. public

def parse_frontend_host_port_from_script(script: str) -> Tuple[Optional[str], Optional[int]]:
    host = None
    port = None
    m = re.search(r"""(?ix)\s--host(?:\s+|=)(?P<host>[A-Za-z0-9\.\-_:]+)""", script)
    if m:
        host = m.group("host").strip()
    m = re.search(r"""(?ix)\s--hostname(?:\s+|=)(?P<host>[A-Za-z0-9\.\-_:]+)""", script)
    if m:
        host = m.group("host").strip()
    m = re.search(r"""(?ix)\s--port(?:\s+|=)(?P<port>\d{{2,5}})""", script)
    if m:
        port = safe_int(m.group("port"))
    if port is None:
        m = re.search(r"""(?ix)\s-p(?:\s+|=)(?P<port>\d{{2,5}})""", script)
        if m:
            port = safe_int(m.group("port"))
    return host, port

def parse_env_port(env_text: str) -> Tuple[Optional[str], Optional[int]]:
    host = None
    port = None
    m = re.search(r"""(?m)^\s*PORT\s*=\s*(\d{2,5})\s*$""", env_text)
    if m:
        port = safe_int(m.group(1))
    if port is None:
        m = re.search(r"""(?m)^\s*VITE_PORT\s*=\s*(\d{2,5})\s*$""", env_text)
        if m:
            port = safe_int(m.group(1))
    m = re.search(r"""(?m)^\s*HOST\s*=\s*([A-Za-z0-9\.\-_:]+)\s*$""", env_text)
    if m:
        host = m.group(1).strip()
    return host, port

def detect_frontend(root: Path, cfg: Dict[str, str]) -> FrontendInfo:
    pkg = None
    for c in FRONTEND_PKG_CANDIDATES:
        p = root / c
        if p.is_file():
            pkg = p
            break
    if not pkg:
        return FrontendInfo(exists=False)

    fe_dir = pkg.parent

    pm = "npm"
    if (fe_dir / "pnpm-lock.yaml").is_file():
        pm = "pnpm"
    elif (fe_dir / "yarn.lock").is_file():
        pm = "yarn"
    elif (fe_dir / "package-lock.json").is_file():
        pm = "npm"

    txt = read_text(pkg)
    def find_script(name: str) -> Optional[str]:
        m = re.search(rf'"{re.escape(name)}"\s*:\s*"([^"]+)"', txt)
        return m.group(1) if m else None

    dev_script = find_script("dev")
    start_script = find_script("start")
    script_name = "dev" if dev_script else ("start" if start_script else "dev")
    chosen_script = dev_script or start_script or ""

    host, port = parse_frontend_host_port_from_script(chosen_script)

    # Detect "static SPA template" convention:
    # If frontend/public/index.html exists, prefer serving that directory directly.
    public_dir = fe_dir / "public"
    is_static_spa = public_dir.is_dir() and (public_dir / "index.html").is_file()

    # If package.json script already uses serve public, treat as static_serve too.
    script_looks_like_serve = bool(re.search(r"(?i)\bserve\b\s+public\b", chosen_script))


    # config override
    if cfg.get("FRONTEND_HOST"):
        host = cfg["FRONTEND_HOST"].strip() or host
    if cfg.get("FRONTEND_PORT"):
        port = safe_int(cfg["FRONTEND_PORT"]) or port


    # .env fallback
    if host is None or port is None:
        for env_name in (".env", ".env.local", ".env.development", ".env.production"):
            env_path = fe_dir / env_name
            if env_path.is_file():
                eh, ep = parse_env_port(read_text(env_path))
                host = host or eh
                port = port or ep
                if host or port:
                    break

    if pm == "npm":
        install_cmd = "npm install"
        run_cmdline = f"npm run {script_name}"
    elif pm == "pnpm":
        install_cmd = "pnpm install"
        run_cmdline = f"pnpm {script_name}"
    else:
        install_cmd = "yarn install"
        run_cmdline = f"yarn {script_name}"

    mode = "node"
    static_dir = ""
    # Convention: FRONTEND_PORT default 5173 (static serve preview)
    port = safe_int(cfg.get("FRONTEND_PORT", "")) or port or 5173
    host = host or "127.0.0.1"

    if is_static_spa or script_looks_like_serve:
        # Use npx serve to preview static SPA without relying on bash-style env expansion.
        mode = "static_serve"
        static_dir = "public"
        install_cmd = ""  # no install required for npx (it can fetch serve)
        run_cmdline = f"npx serve {static_dir} -l {port}"

    return FrontendInfo(
        exists=True,
        dir=str(fe_dir.relative_to(root)).replace("/", "\\") if fe_dir != root else ".",
        pm=pm,
        script=script_name,
        install_cmd=install_cmd,
        run_cmd=run_cmdline,
        host=host,
        port=port,
        mode=mode,
        static_dir=static_dir,
    )

@dataclass
class StaticSiteInfo:
    exists: bool = False
    dir: str = ""
    host: str = "127.0.0.1"
    port: int = 0

def detect_static_site(root: Path, cfg: Dict[str, str]) -> StaticSiteInfo:
    # If frontend exists (package.json), we don't treat it as static by default.
    # Static only when no package.json.
    host = cfg.get("STATIC_HOST", "").strip() or "127.0.0.1"
    port = safe_int(cfg.get("STATIC_PORT", "")) or 0

    for rel in STATIC_SITE_DIR_CANDIDATES:
        d = root / rel
        if d.is_dir():
            idx = d / "index.html"
            if idx.is_file():
                # If port not specified, pick a safe default different from backend default 8000:
                # Still "default" but deterministic. We'll choose 5173 for dist, 3000 for build.
                if port == 0:
                    base = Path(rel).name.lower()
                    port = 5173 if "dist" in rel.lower() else 3000
                return StaticSiteInfo(exists=True, dir=str(d.relative_to(root)).replace("/", "\\"), host=host, port=port)

    return StaticSiteInfo(exists=False)

# ============================================================
# BAT generation (UTF-8 + chcp 65001 + default ports)
# ============================================================

def write_run_app_bat(root: Path, script_name: str, backend: dict,
                      frontend: FrontendInfo,
                      static_site: StaticSiteInfo,
                      cfg: Dict[str, str],
                      venv_dir: str) -> Path:
    mode = backend.get("mode", "none")

    # Backend host/port (BACKEND_* > APP_* > PORT > detected > defaults)
    backend_host, backend_port = resolve_backend_host_port(cfg, backend.get("host"), backend.get("port"))

    start_backend = ""
    backend_url = None

    if mode == "uvicorn":
        target = backend.get("target", "")
        py_path_fix = r'set "PYTHONPATH=%CD%\src;%CD%"' + "\n" if needs_src_pythonpath_for_uvicorn(root, target) else ""
        worker_module = detect_backend_worker_module(root, backend)
        worker_start = ""
        if worker_module:
            worker_start = rf"""echo(啟動後端 Worker（{worker_module}）...
start "Backend Worker" cmd /k ""%PYEXE%" -m {worker_module} 1>>"logs\backend_worker.log" 2>>&1"
"""
        start_backend = rf"""echo([4/6] 啟動後端（uvicorn）...
echo(啟動時間: %DATE% %TIME%
if not exist "logs" mkdir "logs"
{worker_start}{py_path_fix}start "Backend" cmd /k ""%PYEXE%" -m uvicorn {target} --host {backend_host} --port {backend_port} --log-level info 1>>"logs\backend.log" 2>>&1"
"""
        backend_url = f"http://{backend_host}:{backend_port}"

    elif mode == "script":
        script = backend.get("script", "start_backend.py")
        start_backend = rf"""echo([4/6] 啟動後端（{script}）...
echo(啟動時間: %DATE% %TIME%
if not exist "logs" mkdir "logs"
start "Backend" cmd /k ""%PYEXE%" "{script}" 1>>"logs\backend.log" 2>>&1"
"""
        backend_url = f"http://{backend_host}:{backend_port}"

    elif mode == "streamlit":
        entry = backend["file"]
        start_backend = rf"""echo([4/6] 啟動後端（Streamlit）...
echo(啟動時間: %DATE% %TIME%
if not exist "logs" mkdir "logs"
set "PYTHONPATH=%CD%\src;%CD%"
start "Backend" cmd /k ""%PYEXE%" -m streamlit run "{entry}" 1>>"logs\backend.log" 2>>&1"
"""
        # streamlit 不猜 port；除非 cfg 指定 STATIC/FRONTEND 另開
        backend_url = None

    elif mode == "module":
        mod = backend["module"]
        py_path_fix = r'set "PYTHONPATH=%CD%\src;%CD%"' + "\n" if needs_src_pythonpath_fix(root, mod) else ""
        start_backend = rf"""echo([4/6] 啟動後端（python -m {mod}）...
echo(啟動時間: %DATE% %TIME%
if not exist "logs" mkdir "logs"
{py_path_fix}start "Backend" cmd /k ""%PYEXE%" -m {mod} 1>>"logs\backend.log" 2>>&1"
"""
        # module 也不推 url，但你要求「否則後端沒有啟動」：這裡已保證會啟動
        backend_url = None

    else:
        start_backend = r"""echo([4/6] 啟動後端...
echo(
echo( 找不到可用的後端入口，無法啟動。
echo( 建議：
echo(  - 專案內加入 uvicorn 啟動命令（任何檔案皆可被偵測），例如：
echo(     python -m uvicorn your.module:app --host 127.0.0.1 --port 8000
echo( - 或確保存在 src\main.py / src\app.py / backend\main.py 作為入口
echo(
pause
popd
exit /b 1
"""

    # Frontend / Static
    start_frontend = ""
    frontend_url = None

    if frontend.exists:
        if frontend.mode == "static_serve":
            start_frontend = rf"""echo([5/6] 啟動前端（靜態 SPA 預覽）...
if not exist "logs" mkdir "logs"
start "Frontend" cmd /k "cd /d ^"%~dp0{frontend.dir}^" ^&^& {frontend.run_cmd} 1>>^"%~dp0logs\frontend.log^" 2>>&1"
"""

        else:
            start_frontend = rf"""echo([5/6] 啟動前端（Node 專案）...
if not exist "logs" mkdir "logs"
start "Frontend" cmd /k "cd /d ^"%~dp0{frontend.dir}^" ^&^& {frontend.install_cmd} 1>>^"%~dp0logs\frontend.log^" 2>>&1 ^&^& {frontend.run_cmd} 1>>^"%~dp0logs\frontend.log^" 2>>&1"
"""
        # 若偵測不到 port，就不開（避免亂開）
        if frontend.port:
            host = frontend.host or (cfg.get("FRONTEND_HOST", "").strip() or "127.0.0.1")
            frontend_url = f"http://{host}:{frontend.port}"

    elif static_site.exists:
        # 起靜態 server
        static_host = static_site.host
        static_port = static_site.port
        start_frontend = rf"""echo([5/6] 啟動前端（靜態頁面：{static_site.dir}）...
if not exist "logs" mkdir "logs"
start "Frontend" cmd /k "cd /d "{static_site.dir}" ^&^& "%PYEXE%" -m http.server {static_port} --bind {static_host} 1>>"%~dp0logs\frontend.log" 2>>&1"
"""
        frontend_url = f"http://{static_host}:{static_port}"

    else:
        start_frontend = r"""echo([5/6] 前端未偵測到（沒有 package.json，且找不到 dist/build/index.html），略過前端啟動。
"""


    def ensure_endswith_nl(s: str) -> str:
        return s if s.endswith("\n") else (s + "\n")

    # Open browser: backend always open if we have a URL; frontend open if detected
    open_block = "echo([6/6] 自動開啟瀏覽器...\n"
    open_block += "timeout /t 2 >nul\n"
    if backend_url:
        open_block += f'start "" "{backend_url}"\n'
    else:
        open_block += "echo([INFO] 後端未提供可開啟的 URL（可能不是 web server 或無法推斷）。\n"
    if frontend_url:
        open_block += f'start "" "{frontend_url}"\n'

    else:
        open_block += "echo([INFO] 前端未提供可開啟的 URL（未偵測 port 或未啟動）。\n"

    start_backend = ensure_endswith_nl(start_backend)
    start_frontend = ensure_endswith_nl(start_frontend)
    open_block = ensure_endswith_nl(open_block)


    bat_text = rf"""@echo off
chcp 65001 >nul
setlocal ENABLEDELAYEDEXPANSION

REM =========================================
REM 一鍵安裝 / 啟動（穩定版）
REM 檔名固定：run_app.bat（UTF-8 編碼）
REM 注意：本檔會先切換 code page 為 UTF-8（chcp 65001）
REM 由 {script_name} 自動產生
REM =========================================

pushd "%~dp0"

echo(=========================================
echo(  一鍵安裝 / 啟動（穩定版）
echo(=========================================
echo(

echo([1/6] 檢查 Python...
where python >nul 2>&1
if errorlevel 1 (
  echo(
  echo(找不到 Python，請先安裝 Python 3.10 以上。
  echo(下載網址：https://www.python.org/downloads/
  echo(安裝時請勾選 Add Python to PATH
  echo(
  pause
  popd
  exit /b 1
)

echo([2/6] 建立虛擬環境（{venv_dir}）...
if not exist "{venv_dir}\Scripts\python.exe" (
  python -m venv "{venv_dir}"
  if errorlevel 1 (
    echo(
    echo(無法建立虛擬環境。可能原因：權限不足或防毒阻擋。
    echo(建議：右鍵 run_app.bat → 以系統管理員身分執行
    echo(
    pause
    popd
    exit /b 1
  )
)

set "PYEXE=%~dp0{venv_dir}\Scripts\python.exe"

echo([3/6] 自動檢查/修正 + 安裝依賴...
"%PYEXE%" "{script_name}"
if errorlevel 1 (
  echo(
  echo([ERROR] 自動檢查/修正失敗，請看上方輸出訊息。
  echo(
  pause
  popd
  exit /b 1
)

{start_backend}

{start_frontend}

{open_block}

echo(
echo(=========================================
echo(啟動完成。要停止服務請關閉 Backend / Frontend 視窗。
echo(若有錯誤，請將錯誤訊息回傳給 AI 助手。
echo(=========================================
echo(
pause
popd
endlocal
"""
    out_path = root / "run_app.bat"
    write_text_utf8_bom(out_path, bat_text)
    return out_path

# ============================================================
# Full auto pipeline
# ============================================================

def full_auto(root: Path, venv_dir: str) -> Tuple[int, str]:
    ensure_dotenv_from_example(root)
    cfg = get_launcher_config(root)

    scan = scan_imports(root)
    if scan.syntax_errors:
        warn_lines = "\n".join(f"- {norm_rel(root,p)}: {msg}" for p, msg in scan.syntax_errors[:20])
        print("[WARN] 部分檔案語法/解析失敗，可能會漏掃 imports：\n" + warn_lines + ("\n...(略)" if len(scan.syntax_errors) > 20 else ""))

    imported_modules = {iu.module for iu in scan.imports if iu.module}

    # requirements
    req_path = root / "requirements.txt"
    pkgs = filter_third_party_candidates(root, imported_modules)
    if not req_path.exists():
        generate_or_fix_requirements(root, pkgs)
    else:
        req = parse_requirements(req_path)
        stdlib = stdlib_names()
        local = detect_local_toplevel(root)
        bad = {p for p in req.packages if p in stdlib or p in local or p in LOCAL_NAME_BLOCKLIST}
        if bad:
            generate_or_fix_requirements(root, pkgs)

    # venv checks
    try:
        ensure_venv(root, venv_dir)
        pip_install_requirements(root, venv_dir)
        pip_check(root, venv_dir)
        import_test_third_party(root, venv_dir, imported_modules)
    except Exception as e:
        return 1, str(e)

    # detect backend / frontend / static
    backend = detect_backend_mode(root, cfg)
    frontend = detect_frontend(root, cfg)
    static_site = StaticSiteInfo(exists=False)
    if not frontend.exists:
        static_site = detect_static_site(root, cfg)

    # generate bat
    try:
        out = write_run_app_bat(root, Path(__file__).name, backend, frontend, static_site, cfg, venv_dir)
    except Exception as e:
        return 1, f"run_app.bat 生成失敗：{e}"

    # summary
    lines = []
    lines.append("OK：requirements 已自動生成/修正，pip install / pip check / import test 全部通過；已產生 run_app.bat（UTF-8）。")
    lines.append(f"- 後端模式：{backend.get('mode')}")
    if backend.get("mode") == "uvicorn":
        lines.append(f"- uvicorn target：{backend.get('target')}")
        host, port = resolve_backend_host_port(cfg, backend.get("host"), backend.get("port"))
        lines.append(f"- 後端 host/port：{host}:{port}（偵測不到就用預設或設定覆蓋）")
    if backend.get("mode") == "module":
        lines.append(f"- module：{backend.get('module')}（需要時 bat 會自動補 PYTHONPATH=src）")

    if frontend.exists:
        lines.append(f"- 前端（Node）：dir={frontend.dir}, pm={frontend.pm}, script={frontend.script}")
        if frontend.port:
            lines.append(f"- 前端 host/port：{frontend.host or '127.0.0.1'}:{frontend.port}（由 scripts/.env/設定偵測）")
        else:
            lines.append("- 前端 port 未偵測到：仍會啟動，但不自動開前端網址（避免亂開）。")
    elif static_site.exists:
        lines.append(f"- 前端（靜態）：dir={static_site.dir}, host/port={static_site.host}:{static_site.port}")
    else:
        lines.append("- 前端：未偵測 Node 專案，也未偵測靜態站（略過前端啟動）。")

    lines.append(f"- 產出檔案：{out}")
    return 0, "\n".join(lines)

# ============================================================
# CLI
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="All-in-one project launcher (auto-detect uvicorn target/ports; fallback defaults).")
    ap.add_argument("--root", type=str, default=".", help="專案根目錄（預設目前資料夾）")
    ap.add_argument("--venv", type=str, default=DEFAULT_VENV_DIR, help="venv 資料夾（預設 .venv）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    code, msg = full_auto(root, args.venv)
    print(msg)
    return code

if __name__ == "__main__":
    raise SystemExit(main())

# User-provided custom instructions

# Vibe Coding 開發規範
# 請以一級標題列印出"Vibe Coding 開發規範"
# ## **角色與最高原則**
#
# ### **角色定位**
#   - 假設使用者完全不懂程式、環境、佈署。
#   - 你的任務是「幫他把專案寫好」，不是問他技術細節。
#   - 不要丟一堆選擇題（框架、資料庫、狀態管理）給使用者決定，你自己選一個合適的方案做到底。
#   - 當使用者有提供規格時請務必照規格開發，若有提供介面範例svg則務必確保介面外觀一致，若有困難則需要事前與使用者討論不得擅自修改方向
#
# ### **修改原則**
#   若是對既有專案做修改：
#   - 優先「最小修改」來解 bug / 加功能。
#   - 儘量不破壞原有 API / 資料結構 / 前端路由。
#   - 不要把整個架構翻掉，除非使用者明說要重構或換技術。
#
# ### **預設行為**
#   - 介面使用繁體中文以及台灣用語，除非使用者指定否則淺色模式'
#   - 只要"新增"物件的功能，就應該要做出來完整的CRUD
#   - 只要有上傳檔案功能應該就要有連帶的預覽機制(不管是PDF,IMAGE,VIDEO...)
#   - 若使用llm請使用streaming，並以繁體中文對話
#   - 所有圖片縮放時都需要按照原始寬高比
#   - 所有長時間任務都需要有進度條，也不可以造成ui鎖定卡住
#   - 根據應用屬性判斷是一次性使用還是會有接續使用的需求，如果是後者請需要考慮session 屬性以及規劃持久化
#   - toast來呈現錯誤訊息要有足夠的停駐時間以及讓toast內容變成可以被選取複製的
#   - 涉及使用者付費服務需要與使用者確認
#   - 所有金鑰皆存放於環境變數中
#   - 若有使用mocking，則應該在每次對話都要反覆提醒使用者mocking的存在
# ---
#
# ## **錯誤處理、Logging**
#
# - **錯誤不能默默吞掉**：  
#   - 主程式入口必須有全域例外處理（例如 `if __name__ == "__main__":` 中用 `try...except` 包裹）。  
#   - 禁止在發生異常時完全無log就關閉應用程式。  
#
# - **防崩潰設計**：  
#   - 檔案 I/O、網路請求、模型推論等關鍵流程必須加上 `try-except`，避免直接閃退。  
#
# - **雙層訊息設計**：  
#   - **UI 給人類看**：  
#     - 例如：「檔案讀取失敗，請確認檔案是否仍存在或權限是否允許」。  
#   - **Log 給工程師看**：  
#     - 寫入 `logs/` 目錄，包含時間戳、日誌等級、Stack Trace 等完整技術資訊。  
# - **日誌規範**：  
#   - 分級（例如 INFO / WARNING / ERROR）。  
#   - 嚴禁在 log 中記錄任何敏感資料（API Key、密碼、Token、使用者隱私內容）。  
