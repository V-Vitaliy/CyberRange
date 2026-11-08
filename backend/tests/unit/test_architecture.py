"""Module boundaries of backend/app, checked statically (no imports are executed)."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# Domains already moved into app/<domain>/ (ARC-3); their repositories are private.
MIGRATED_DOMAINS = {
    "auth": ["UserRepository"],
    "chat": ["ChatRepository"],
    "ctf": ["CtfRepository"],
    "defense": [],
    "documents": [],
    "labs": ["GameSessionRepository"],
    "siem": ["AuditLogRepository"],
}

# Route path -> the domain whose router.py must declare it.
ROUTE_OWNERS = {
    "/login": "auth",
    "/submit": "ctf",
    "/defenses/buy": "defense",
    "/me": "auth",
    "/investigate": "siem",
    "/dashboard": "siem",
    "/logs": "siem",
    "/chat/ask": "chat",
    "/chat/history": "chat",
    "/chat/threads": "chat",
    "/upload": "documents",
    "/ws/etl-status/{task_id}": "documents",
}
ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete", "websocket"}


def _modules():
    for path in sorted(APP.rglob("*.py")):
        yield path.relative_to(APP), ast.parse(path.read_text(), filename=str(path))


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, []


def _names(tree):
    """Names used or imported in a module, including attribute names."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("domain", sorted(MIGRATED_DOMAINS))
def test_domain_repository_is_used_only_inside_its_domain(domain):
    offenders = [
        f"{path}: imports {module}"
        for path, tree in _modules()
        if path.parts[0] != domain
        for module, _ in _imports(tree)
        if module == f"app.{domain}.repository" or module.startswith(f"app.{domain}.repository.")
    ]
    assert offenders == [], "use the domain's service instead:\n" + "\n".join(offenders)


@pytest.mark.parametrize("domain", sorted(MIGRATED_DOMAINS))
def test_moved_repository_classes_are_not_referenced_outside_their_domain(domain):
    offenders = [
        f"{path}: {name}"
        for path, tree in _modules()
        if path.parts[0] != domain
        for name in sorted(_names(tree) & set(MIGRATED_DOMAINS[domain]))
    ]
    assert offenders == [], "\n".join(offenders)


def test_routers_do_not_import_domain_repositories():
    routers = [(path, tree) for path, tree in _modules() if path.name == "router.py"]
    offenders = [
        f"{path}: imports {module}"
        for path, tree in routers
        for module, _ in _imports(tree)
        if any(module == f"app.{domain}.repository" for domain in MIGRATED_DOMAINS)
    ]
    assert offenders == [], "\n".join(offenders)


@pytest.mark.parametrize("domain", sorted(MIGRATED_DOMAINS))
def test_migrated_domain_package_exists(domain):
    assert (APP / domain / "__init__.py").is_file()
    assert (APP / domain / "service.py").is_file()
    if MIGRATED_DOMAINS[domain]:
        assert (APP / domain / "repository.py").is_file()


def test_core_imports_no_repository():
    offenders = [
        f"{path}: imports {module}"
        for path, tree in _modules()
        if path.parts[0] == "core"
        for module, _ in _imports(tree)
        if module.split(".")[-1] == "repository"
    ]
    assert offenders == [], "\n".join(offenders)


def _route_declarations():
    for path, tree in _modules():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ROUTE_DECORATORS
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                yield path, node.lineno, node.args[0].value


@pytest.mark.parametrize("route", sorted(ROUTE_OWNERS))
def test_routes_are_declared_in_their_domain_router(route):
    owner = Path(ROUTE_OWNERS[route]) / "router.py"
    found = [(path, line) for path, line, value in _route_declarations() if value == route]
    assert found, f"{route} is not declared anywhere"
    assert [f"{path}:{line}" for path, line in found if path != owner] == []


SCRIPTS = APP.parents[1] / "scripts"


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.py")), ids=lambda path: path.name)
def test_scripts_that_create_tables_also_apply_schema_upgrades(script):
    """create_all leaves existing tables as they are; columns added later come from SCHEMA_UPGRADES."""
    names = _names(ast.parse(script.read_text(), filename=str(script)))
    if "create_all" in names:
        assert "SCHEMA_UPGRADES" in names, f"{script.name} creates tables without the column upgrades"


@pytest.mark.parametrize("legacy", ["api", "services", "schemas", "db/repository.py"])
def test_pre_domain_layers_are_gone(legacy):
    assert not (APP / legacy).exists(), f"{legacy} should have been split into domain packages"
