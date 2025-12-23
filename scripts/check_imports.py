from __future__ import annotations

from pathlib import Path
import sys

from import_scanner import collect_imports, iter_python_files, local_top_level_modules, module_exists
from requirements_utils import parse_requirements, requirement_module_map


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    requirements_path = repo_root / "requirements.txt"
    requirements = parse_requirements(requirements_path)
    requirement_modules = requirement_module_map(requirements)
    allowed_third_party = {module.lower() for modules in requirement_modules.values() for module in modules}

    stdlib_modules = {name.lower() for name in sys.stdlib_module_names} | {
        name.lower() for name in sys.builtin_module_names
    }
    local_modules = {name.lower() for name in local_top_level_modules(repo_root)}

    errors: list[str] = []
    for path in iter_python_files(repo_root):
        for entry in collect_imports(path):
            if entry.is_from:
                module = entry.module
                if entry.level:
                    continue
                if not module:
                    continue
                top_level = module.split(".")[0].lower()
                if not _is_allowed_module(
                    repo_root, top_level, module, stdlib_modules, local_modules, allowed_third_party
                ):
                    errors.append(
                        f"{path}:{entry.lineno} Unknown import source '{module}' (not stdlib/local/requirements)."
                    )
            else:
                for name in entry.names:
                    top_level = name.split(".")[0].lower()
                    if not _is_allowed_module(
                        repo_root, top_level, name, stdlib_modules, local_modules, allowed_third_party
                    ):
                        errors.append(
                            f"{path}:{entry.lineno} Unknown import source '{name}' (not stdlib/local/requirements)."
                        )

    if errors:
        print("Import correctness check failed:")
        for error in sorted(errors):
            print(f"  - {error}")
        return 1
    print("Import correctness check passed.")
    return 0


def _is_allowed_module(
    repo_root: Path,
    top_level: str,
    module: str,
    stdlib_modules: set[str],
    local_modules: set[str],
    allowed_third_party: set[str],
) -> bool:
    if top_level in stdlib_modules:
        return True
    if top_level in local_modules:
        return module_exists(repo_root, module)
    return top_level in allowed_third_party


if __name__ == "__main__":
    raise SystemExit(main())
