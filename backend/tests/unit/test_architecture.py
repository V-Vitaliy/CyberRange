"""Module boundaries of backend/app, checked statically (no imports are executed)."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# Domains already moved into app/<domain>/ (ARC-3); their repositories are private.
MIGRATED_DOMAINS = {"labs": ["GameSessionRepository"]}


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
    routers = [(path, tree) for path, tree in _modules() if path.parts[0] == "api" or path.name == "router.py"]
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
    assert (APP / domain / "repository.py").is_file()
    assert (APP / domain / "service.py").is_file()
