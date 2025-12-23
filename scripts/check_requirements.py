from __future__ import annotations

from pathlib import Path
import sys

from import_scanner import collect_imports, iter_python_files, local_top_level_modules
from requirements_utils import parse_requirements, requirement_module_map


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    requirements_path = repo_root / "requirements.txt"
    requirements = parse_requirements(requirements_path)
    requirement_modules = requirement_module_map(requirements)

    stdlib_modules = {name.lower() for name in sys.stdlib_module_names} | {
        name.lower() for name in sys.builtin_module_names
    }
    local_modules = {name.lower() for name in local_top_level_modules(repo_root)}

    imported_modules = _collect_third_party_modules(repo_root, stdlib_modules, local_modules)
    allowed_modules = {module for modules in requirement_modules.values() for module in modules}

    missing = sorted(mod for mod in imported_modules if mod not in allowed_modules)
    unused = sorted(
        req for req, modules in requirement_modules.items() if not any(module in imported_modules for module in modules)
    )

    exit_code = 0
    if missing:
        exit_code = 1
        print("Missing dependencies in requirements.txt:")
        for module in missing:
            print(f"  - {module}")
    else:
        print("No missing dependencies in requirements.txt.")

    if unused:
        for req in unused:
            print(f"::warning::Unused dependency in requirements.txt: {req}")
    else:
        print("No unused dependencies in requirements.txt.")

    return exit_code


def _collect_third_party_modules(
    repo_root: Path, stdlib_modules: set[str], local_modules: set[str]
) -> set[str]:
    modules: set[str] = set()
    for path in iter_python_files(repo_root):
        for entry in collect_imports(path):
            if entry.is_from:
                if entry.level:
                    continue
                module = entry.module or ""
                if not module:
                    continue
                top_level = module.split(".")[0].lower()
                _add_if_third_party(modules, top_level, stdlib_modules, local_modules)
            else:
                for name in entry.names:
                    top_level = name.split(".")[0].lower()
                    _add_if_third_party(modules, top_level, stdlib_modules, local_modules)
    return modules


def _add_if_third_party(
    modules: set[str], top_level: str, stdlib_modules: set[str], local_modules: set[str]
) -> None:
    if top_level in stdlib_modules:
        return
    if top_level in local_modules:
        return
    modules.add(top_level)


if __name__ == "__main__":
    raise SystemExit(main())
