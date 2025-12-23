from __future__ import annotations

from dataclasses import dataclass
import ast
from pathlib import Path
from typing import Iterable, List


@dataclass(frozen=True)
class ImportEntry:
    module: str | None
    names: List[str]
    level: int
    file: Path
    lineno: int
    is_from: bool


EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", ".github", ".mypy_cache"}


def iter_python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        yield path


def local_top_level_modules(repo_root: Path) -> set[str]:
    modules: set[str] = set()
    for path in iter_python_files(repo_root):
        base = repo_root / "src" if (repo_root / "src") in path.parents else repo_root
        rel = path.relative_to(base)
        if rel.parts:
            top_level = rel.parts[0]
            if top_level.endswith(".py"):
                top_level = Path(top_level).stem
            modules.add(top_level)
        if path.parent == repo_root / "scripts":
            modules.add(path.stem)
    return modules


def module_exists(repo_root: Path, module_name: str) -> bool:
    parts = module_name.split(".")
    for base in (repo_root / "src", repo_root, repo_root / "scripts"):
        module_path = base.joinpath(*parts)
        if module_path.with_suffix(".py").exists():
            return True
        if module_path.is_dir() and (module_path / "__init__.py").exists():
            return True
    return False


def collect_imports(path: Path) -> list[ImportEntry]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    collector = _ImportCollector(path)
    collector.visit(tree)
    return collector.imports


class _ImportCollector(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self._path = path
        self.imports: list[ImportEntry] = []
        self._type_checking_stack = [False]
        self._optional_stack = [False]

    def visit_If(self, node: ast.If) -> None:
        parent_state = self._type_checking_stack[-1]
        is_tc = _is_type_checking_test(node.test)
        self._type_checking_stack.append(parent_state or is_tc)
        for child in node.body:
            self.visit(child)
        self._type_checking_stack.pop()

        self._type_checking_stack.append(parent_state)
        for child in node.orelse:
            self.visit(child)
        self._type_checking_stack.pop()

    def visit_Try(self, node: ast.Try) -> None:
        parent_state = self._optional_stack[-1]
        handles_import_error = any(_is_import_error_handler(handler) for handler in node.handlers)

        self._optional_stack.append(parent_state or handles_import_error)
        for child in node.body:
            self.visit(child)
        self._optional_stack.pop()

        self._optional_stack.append(parent_state)
        for child in node.orelse:
            self.visit(child)
        for child in node.finalbody:
            self.visit(child)
        for handler in node.handlers:
            self.visit(handler)
        self._optional_stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        if self._should_skip():
            return
        names = [alias.name for alias in node.names]
        self.imports.append(
            ImportEntry(
                module=None,
                names=names,
                level=0,
                file=self._path,
                lineno=node.lineno,
                is_from=False,
            )
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._should_skip():
            return
        names = [alias.name for alias in node.names]
        self.imports.append(
            ImportEntry(
                module=node.module,
                names=names,
                level=node.level or 0,
                file=self._path,
                lineno=node.lineno,
                is_from=True,
            )
        )

    def _should_skip(self) -> bool:
        return self._type_checking_stack[-1] or self._optional_stack[-1]


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return isinstance(test.value, ast.Name) and test.value.id in {"typing", "typing_extensions"}
    return False


def _is_import_error_handler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False
    if isinstance(handler.type, ast.Name):
        return handler.type.id in {"ImportError", "ModuleNotFoundError"}
    if isinstance(handler.type, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id in {"ImportError", "ModuleNotFoundError"}
            for elt in handler.type.elts
        )
    return False
