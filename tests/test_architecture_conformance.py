from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HANDLERS_DIR = PROJECT_ROOT / "aird" / "handlers"
SERVICES_DIR = PROJECT_ROOT / "aird" / "services"


def _py_files(path: Path) -> list[Path]:
    return sorted([p for p in path.glob("*.py") if p.name != "__init__.py"])


# base_handler.py is allowed to import from aird.db (it provides get_user_by_username
# lookup used by get_current_user).
_DB_IMPORT_WHITELIST = {"base_handler.py"}


def test_no_direct_db_imports_in_handlers():
    """Handlers must go through services, never aird.db (except whitelisted modules)."""
    offenders: list[str] = []
    for path in _py_files(HANDLERS_DIR):
        if path.name in _DB_IMPORT_WHITELIST:
            continue
        content = path.read_text(encoding="utf-8")
        if "from aird.db import" in content or "import aird.db" in content:
            offenders.append(path.name)
    assert not offenders, f"Direct aird.db imports found in handlers: {offenders}"


def test_services_do_not_import_handlers():
    """Services should not depend on delivery layer types."""
    offenders: list[str] = []
    for path in _py_files(SERVICES_DIR):
        content = path.read_text(encoding="utf-8")
        if "from aird.handlers" in content or "import aird.handlers" in content:
            offenders.append(path.name)
    assert not offenders, f"Services importing handlers: {offenders}"


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
        "Unexpected cross-handler imports found (should go through services): "
        f"{offenders}"
    )
