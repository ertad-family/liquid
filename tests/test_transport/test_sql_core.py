"""Shared SQL toolkit: dialect-aware query building, DSN handling, coercion."""

from __future__ import annotations

from liquid.transport._sql import (
    DUCKDB,
    MSSQL,
    MYSQL,
    POSTGRES,
    SQLITE,
    SelectBuilder,
    build_plain_select,
    inject_password,
    is_dsn,
    quote_ident,
    redact_dsn,
    relation,
)

META = {"schema": "app", "table": "users", "columns": ["id", "name"]}
RESERVED = frozenset({"limit", "offset", "__cursor__"})


def test_placeholders_per_dialect():
    assert SelectBuilder(POSTGRES).add_param(1) == "$1"
    assert SelectBuilder(SQLITE).add_param(1) == "?"
    assert SelectBuilder(MYSQL).add_param(1) == "%s"
    b = SelectBuilder(POSTGRES)
    assert [b.add_param(x) for x in (1, 2, 3)] == ["$1", "$2", "$3"]


def test_quote_ident_per_dialect():
    assert quote_ident("col", POSTGRES) == '"col"'
    assert quote_ident("col", SQLITE) == '"col"'
    assert quote_ident("col", MYSQL) == "`col`"
    # backtick escaping for MySQL
    assert quote_ident("we`ird", MYSQL) == "`we``ird`"


def test_relation_with_and_without_schema():
    assert relation("app", "users", MYSQL) == "`app`.`users`"
    assert relation(None, "users", SQLITE) == '"users"'


def test_build_plain_select_sqlite():
    sql, args, limit, offset = build_plain_select(META, {"limit": 5}, "10", SQLITE, RESERVED)
    assert sql == 'SELECT * FROM "app"."users" LIMIT ? OFFSET ?'
    assert args == [5, 10]
    assert (limit, offset) == (5, 10)


def test_build_plain_select_mysql_with_filter():
    sql, args, *_ = build_plain_select(META, {"name": "alice"}, None, MYSQL, RESERVED)
    assert sql == "SELECT * FROM `app`.`users` WHERE `name` = %s LIMIT %s OFFSET %s"
    assert args == ["alice", 1000, 0]


def test_build_plain_select_ignores_unknown_keys():
    sql, args, *_ = build_plain_select(META, {"bogus": 1, "limit": 2}, None, SQLITE, RESERVED)
    assert "WHERE" not in sql
    assert args == [2, 0]


def test_build_plain_select_duckdb():
    sql, args, *_ = build_plain_select(META, {"limit": 3}, None, DUCKDB, RESERVED)
    assert sql == 'SELECT * FROM "app"."users" LIMIT ? OFFSET ?'
    assert args == [3, 0]


def test_quote_ident_mssql_brackets():
    assert quote_ident("col", MSSQL) == "[col]"
    # the closing bracket is what gets escaped
    assert quote_ident("we]ird", MSSQL) == "[we]]ird]"


def test_build_plain_select_mssql_offset_fetch():
    sql, args, *_ = build_plain_select(META, {"limit": 5}, "10", MSSQL, RESERVED)
    assert sql == "SELECT * FROM [app].[users] ORDER BY (SELECT NULL) OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    assert args == [10, 5]  # MSSQL binds offset before the row count


def test_build_plain_select_mssql_with_filter():
    sql, args, *_ = build_plain_select(META, {"name": "x"}, None, MSSQL, RESERVED)
    assert sql.startswith("SELECT * FROM [app].[users] WHERE [name] = ?")
    assert sql.endswith("OFFSET ? ROWS FETCH NEXT ? ROWS ONLY")
    assert args == ["x", 0, 1000]


def test_is_dsn():
    assert is_dsn("mysql://h/db", ("mysql://",))
    assert not is_dsn("https://x", ("mysql://",))
    assert not is_dsn(None, ("mysql://",))


def test_redact_and_inject_roundtrip():
    full = "mysql://user:secret@host:3306/db"
    redacted = redact_dsn(full)
    assert redacted == "mysql://user@host:3306/db"
    assert inject_password(redacted, "secret") == full
