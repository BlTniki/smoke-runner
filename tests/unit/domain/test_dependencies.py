"""Architecture guard for the pure domain package."""

import ast
import sys
from pathlib import Path


def test_domain_imports_only_standard_library_or_other_domain_modules() -> None:
    project_root = Path(__file__).resolve().parents[3]
    domain_root = project_root / "src" / "smoke_runner" / "domain"

    for source_path in domain_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules = [node.module]
            else:
                continue

            for module_name in imported_modules:
                top_level = module_name.partition(".")[0]
                is_standard_library = top_level in sys.stdlib_module_names
                is_domain_module = module_name == "smoke_runner.domain" or module_name.startswith(
                    "smoke_runner.domain."
                )
                assert is_standard_library or is_domain_module, (
                    f"{source_path.name} imports forbidden dependency {module_name}"
                )
