from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HANDLERS_DIR = PROJECT_ROOT / "aird" / "handlers"
SERVICES_DIR = PROJECT_ROOT / "aird" / "services"
REPOSITORIES_DIR = PROJECT_ROOT / "aird" / "repositories"


def _py_files(path: Path) -> list[Path]:
    return sorted([p for p in path.glob("*.py") if p.name != "__init__.py"])


def test_no_direct_db_imports_in_handlers():
    """Handlers must go through services/repositories/adapters, never aird.db."""
    offenders: list[str] = []
    for path in _py_files(HANDLERS_DIR):
        content = path.read_text(encoding="utf-8")
        if "from aird.db import" in content or "import aird.db" in content:
            offenders.append(path.name)
    assert not offenders, f"Direct aird.db imports found in handlers: {offenders}"


def test_handlers_do_not_import_repositories_directly():
    """Delivery layer should not bypass service boundaries."""
    offenders: list[str] = []
    for path in _py_files(HANDLERS_DIR):
        content = path.read_text(encoding="utf-8")
        if "from aird.repositories" in content or "import aird.repositories" in content:
            offenders.append(path.name)
    assert not offenders, f"Handlers importing repositories directly: {offenders}"


def test_services_do_not_import_handlers():
    """Services should not depend on delivery layer types."""
    offenders: list[str] = []
    for path in _py_files(SERVICES_DIR):
        content = path.read_text(encoding="utf-8")
        if "from aird.handlers" in content or "import aird.handlers" in content:
            offenders.append(path.name)
    assert not offenders, f"Services importing handlers: {offenders}"


def test_repositories_do_not_import_handlers_or_services():
    """Repositories are low-level adapters only."""
    offenders: list[str] = []
    for path in _py_files(REPOSITORIES_DIR):
        content = path.read_text(encoding="utf-8")
        if (
            "from aird.handlers" in content
            or "import aird.handlers" in content
            or "from aird.services" in content
            or "import aird.services" in content
        ):
            offenders.append(path.name)
    assert not offenders, f"Repositories with invalid dependencies: {offenders}"


def test_handler_cross_feature_imports_are_controlled():
    """Limit handler-to-handler imports to explicit transitional allowlist."""
    transitional_allowlist = {
        "admin_handlers.py": ["from aird.handlers.api_handlers import"],
        "share_handlers.py": ["from aird.handlers.view_handlers import"],
    }
    offenders: list[str] = []
    for path in _py_files(HANDLERS_DIR):
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            if "from aird.handlers." not in line:
                continue
            if "base_handler" in line or "constants" in line:
                continue
            allowed_prefixes = transitional_allowlist.get(path.name, [])
            stripped = line.strip()
            if not any(stripped.startswith(prefix) for prefix in allowed_prefixes):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        "Unexpected cross-handler imports found (should go through services/adapters): "
        f"{offenders}"
    )
