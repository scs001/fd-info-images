#!/usr/bin/env python3
"""Regenerate the bundled registry.db + registry.json from the scripts' MANIFESTs.

The MANIFEST is the source of truth; the DB is derived. Idempotent: re-running
produces a byte-identical DB (delete-then-insert per source, deterministic
JSON sort_keys).

Usage: `python -m fd_cn_gov.build_registry` or `fd-cn-gov build-registry`.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

# The 11 ministry scrapers, alphabetical by module name (deterministic order).
SCRIPT_NAMES = [
    "mee_gsgg_archive",
    "mem_tzgg_archive",
    "mnr_tzgg_archive",
    "moa_govpublic_archive",
    "mof_gkml_archive",
    "mofcom_xwfb_archive",
    "mohurd_xinwen_archive",
    "mot_shuju_archive",
    "ndrc_tzgg_archive",
    "pbc_xinwen_archive",
    "safe_whxw_archive",
]

REGISTRY_DIR = Path(__file__).resolve().parent / "registry"
DB_PATH = REGISTRY_DIR / "registry.db"
JSON_PATH = REGISTRY_DIR / "registry.json"


def _load_manifests() -> list:
    manifests = []
    for name in SCRIPT_NAMES:
        mod = importlib.import_module(f"fd_cn_gov.scripts.{name}")
        m = getattr(mod, "MANIFEST", None)
        if m is None or m.name != name:
            raise RuntimeError(f"{name}: MANIFEST missing or name mismatch")
        manifests.append(m)
    return manifests


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS datasource_columns;
        DROP TABLE IF EXISTS scraw_configs;
        DROP TABLE IF EXISTS sources;

        CREATE TABLE sources (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            label           TEXT NOT NULL,
            url             TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            category        TEXT NOT NULL DEFAULT '',
            category_label  TEXT NOT NULL DEFAULT '',
            config_json     TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE datasource_columns (
            id              INTEGER PRIMARY KEY,
            datasource_id   INTEGER NOT NULL,
            table_name      TEXT NOT NULL,
            column_name     TEXT NOT NULL,
            column_type     TEXT NOT NULL,
            is_primary_key  INTEGER NOT NULL DEFAULT 0,
            is_nullable     INTEGER NOT NULL DEFAULT 1,
            description     TEXT NOT NULL DEFAULT '',
            source_field    TEXT NOT NULL DEFAULT '',
            unit            TEXT NOT NULL DEFAULT '',
            semantic_type   TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE scraw_configs (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            url             TEXT NOT NULL,
            columns_json    TEXT NOT NULL DEFAULT '[]'
        );
        """
    )


def _write_source(conn: sqlite3.Connection, source_id: int, m) -> None:
    conn.execute(
        """INSERT INTO sources
           (id, name, label, url, description, category, category_label, config_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            source_id, m.name, m.label, m.url, m.description,
            m.category, m.category_label, m.to_config_json(),
        ),
    )
    cols = m.to_columns_json()
    for c in cols:
        conn.execute(
            """INSERT INTO datasource_columns
               (datasource_id, table_name, column_name, column_type,
                is_primary_key, is_nullable, description, source_field,
                unit, semantic_type)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                source_id, c["table_name"], c["column_name"], c["column_type"],
                c["is_primary_key"], c["is_nullable"], c["description"],
                c["source_field"], c["unit"], c["semantic_type"],
            ),
        )
    conn.execute(
        "INSERT INTO scraw_configs (name, url, columns_json) VALUES (?,?,?)",
        (m.name, m.url, json.dumps(m.to_scraw_columns(), ensure_ascii=False)),
    )


def _dump_json(conn: sqlite3.Connection, manifests: list) -> dict:
    """Deterministic full dump for registry.json (sort_keys, ensure_ascii=False)."""
    sources = [
        {
            "id": i + 1,
            "name": m.name,
            "label": m.label,
            "url": m.url,
            "description": m.description,
            "category": m.category,
            "category_label": m.category_label,
            "config": m.to_config(),
        }
        for i, m in enumerate(manifests)
    ]
    cols = []
    with conn:
        for row in conn.execute(
            """SELECT datasource_id, table_name, column_name, column_type,
                      is_primary_key, is_nullable, description, source_field,
                      unit, semantic_type
               FROM datasource_columns ORDER BY datasource_id, rowid"""
        ):
            cols.append(dict(row))
    scraw = []
    with conn:
        for row in conn.execute(
            "SELECT name, url, columns_json FROM scraw_configs ORDER BY name"
        ):
            d = dict(row)
            d["columns_json"] = json.loads(d["columns_json"])
            scraw.append(d)
    return {"sources": sources, "datasource_columns": cols, "scraw_configs": scraw}


def build() -> str:
    manifests = _load_manifests()
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            _create_schema(conn)
            for i, m in enumerate(manifests, start=1):
                _write_source(conn, i, m)
        dump = _dump_json(conn, manifests)
    finally:
        conn.close()
    JSON_PATH.write_text(
        json.dumps(dump, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    n_cols = sum(len(m.to_columns_json()) for m in manifests)
    return (
        f"built {DB_PATH} + {JSON_PATH}: "
        f"{len(manifests)} sources, {n_cols} columns"
    )


def main() -> int:
    print(build())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
