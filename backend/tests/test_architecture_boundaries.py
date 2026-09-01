"""Keep dependencies flowing inward in the modular monolith."""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).parents[1] / "app"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_core_has_no_outer_layer_imports():
    forbidden = (
        "fastapi",
        "sqlalchemy",
        "app.api",
        "app.models",
        "app.services",
        "app.application",
    )
    violations = []
    for path in (APP_ROOT / "core").rglob("*.py"):
        for imported in _imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(APP_ROOT)} -> {imported}")
    assert not violations, violations


def test_domain_has_no_framework_or_outer_layer_imports():
    forbidden = ("fastapi", "sqlalchemy", "app.api", "app.models", "app.services")
    violations = []
    for path in (APP_ROOT / "domain").rglob("*.py"):
        for imported in _imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(APP_ROOT)} -> {imported}")
    assert not violations, violations


def test_application_does_not_import_api():
    application = APP_ROOT / "application"
    if not application.exists():
        return
    violations = [
        f"{path.relative_to(APP_ROOT)} -> {imported}"
        for path in application.rglob("*.py")
        for imported in _imports(path)
        if imported.startswith("app.api")
    ]
    assert not violations, violations


def test_services_do_not_import_application():
    violations = [
        f"{path.relative_to(APP_ROOT)} -> {imported}"
        for path in (APP_ROOT / "services").rglob("*.py")
        for imported in _imports(path)
        if imported.startswith("app.application")
    ]
    assert not violations, violations


def test_infrastructure_does_not_import_routes():
    violations = [
        f"{path.relative_to(APP_ROOT)} -> {imported}"
        for folder in ("db", "models", "services")
        for path in (APP_ROOT / folder).rglob("*.py")
        for imported in _imports(path)
        if imported.startswith("app.api")
    ]
    assert not violations, violations

