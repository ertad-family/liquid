"""SQL write builders: INSERT / UPDATE / DELETE — parameterized, column-validated,
dialect-aware, with required WHERE for update/delete (pure)."""

from __future__ import annotations

import pytest

from liquid.transport._sql import (
    MYSQL,
    POSTGRES,
    SQLITE,
    WriteError,
    affected_from_status,
    build_delete,
    build_insert,
    build_update,
    build_write,
)

META = {"schema": "public", "table": "users", "columns": ["id", "name", "age"]}


def test_build_insert_sqlite():
    sql, args = build_insert(META, {"name": "a", "age": 3}, SQLITE)
    assert sql == 'INSERT INTO "public"."users" ("name", "age") VALUES (?, ?)'
    assert args == ["a", 3]


def test_build_insert_postgres_numeric_placeholders():
    sql, args = build_insert(META, {"name": "a", "age": 3}, POSTGRES)
    assert sql == 'INSERT INTO "public"."users" ("name", "age") VALUES ($1, $2)'
    assert args == ["a", 3]


def test_build_insert_drops_unknown_columns():
    sql, args = build_insert(META, {"name": "a", "bogus": 1}, SQLITE)
    assert "bogus" not in sql
    assert args == ["a"]


def test_build_insert_no_known_columns_raises():
    with pytest.raises(WriteError):
        build_insert(META, {"bogus": 1}, SQLITE)


def test_build_update_set_then_where_order():
    sql, args = build_update(META, {"name": "b", "age": 9}, {"id": 7}, POSTGRES)
    assert sql == 'UPDATE "public"."users" SET "name" = $1, "age" = $2 WHERE "id" = $3'
    assert args == ["b", 9, 7]


def test_build_update_empty_where_raises():
    # No blanket updates — a non-empty WHERE on known columns is required.
    with pytest.raises(WriteError):
        build_update(META, {"name": "b"}, {}, SQLITE)


def test_build_delete_mysql():
    sql, args = build_delete(META, {"id": 7}, MYSQL)
    assert sql == "DELETE FROM `public`.`users` WHERE `id` = %s"
    assert args == [7]


def test_build_delete_empty_where_raises():
    with pytest.raises(WriteError):
        build_delete(META, {}, SQLITE)


def test_build_write_dispatch_and_bad_op():
    assert build_write("insert", META, {"name": "a"}, {}, SQLITE)[0].startswith("INSERT")
    assert build_write("update", META, {"name": "a"}, {"id": 1}, SQLITE)[0].startswith("UPDATE")
    assert build_write("delete", META, {}, {"id": 1}, SQLITE)[0].startswith("DELETE")
    with pytest.raises(WriteError):
        build_write("upsert", META, {}, {}, SQLITE)


def test_affected_from_status():
    assert affected_from_status("UPDATE 3") == 3
    assert affected_from_status("INSERT 0 1") == 1
    assert affected_from_status("DELETE 0") == 0
    assert affected_from_status("BEGIN") is None
