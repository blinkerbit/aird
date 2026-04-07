"""Helpers for SQL fragments built only from allow-listed identifiers and tables."""

from collections.abc import Sequence

_UPDATE_TABLES = frozenset({"ldap_configs", "shares", "users", "network_shares"})


def format_select_columns(columns: Sequence[str], allowed: frozenset[str]) -> str:
    """Return a comma-separated column list; every name must be in ``allowed``."""
    col_list = list(columns)
    for name in col_list:
        if name not in allowed:
            msg = f"disallowed SQL column: {name!r}"
            raise ValueError(msg)
    return ", ".join(col_list)


def format_update_by_id_sql(table: str, assignments_joined: str) -> str:
    """Build ``UPDATE <allow-listed table> SET ... WHERE id = ?``."""
    if table not in _UPDATE_TABLES:
        msg = f"disallowed SQL table for update: {table!r}"
        raise ValueError(msg)
    return (
        "UPDATE " + table + " SET " + assignments_joined + " WHERE id = ?"
    )  # nosec B608


def format_shares_select_sql(columns_csv: str) -> str:
    """``SELECT <cols> FROM shares`` (columns_csv pre-validated)."""
    return "SELECT " + columns_csv + " FROM shares"  # nosec B608


def format_shares_select_by_id_sql(columns_csv: str) -> str:
    """``SELECT <cols> FROM shares WHERE id = ?``."""
    return format_shares_select_sql(columns_csv) + " WHERE id = ?"
