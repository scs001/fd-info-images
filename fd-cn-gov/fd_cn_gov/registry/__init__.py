"""Read API over the bundled registry.db.

`list_sources()`, `get_source(name)`, `get_columns(name)` open the bundled
SQLite read-only and return dataclasses. The DB is a derived artifact built by
`fd_cn_gov.build_registry` from the scripts' MANIFESTs.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DB_PATH = Path(__file__).resolve().parent / "registry.db"


@dataclass
class Source:
    name: str
    label: str
    url: str
    description: str
    category: str


@dataclass
class Column:
    name: str
    type: str
    primary_key: bool
    nullable: bool
    description: str
    source_field: str
    unit: str
    semantic_type: str


def _connect() -> sqlite3.Connection:
    if not _DB_PATH.exists():
        raise RuntimeError(
            f"registry.db not found at {_DB_PATH}. "
            f"Run `fd-cn-gov build-registry` to generate it."
        )
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_sources() -> list[Source]:
    """All registered ministry sources."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, label, url, description, category FROM sources ORDER BY name"
        ).fetchall()
    return [Source(**r) for r in rows]


def get_source(name: str) -> Source:
    """One source by name; raises KeyError if unknown."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT name, label, url, description, category FROM sources WHERE name=?",
            (name,),
        ).fetchone()
    if row is None:
        raise KeyError(f"unknown source: {name!r}; see list_sources()")
    return Source(**row)


def get_columns(name: str) -> list[Column]:
    """The column schema for one source; raises KeyError if the source is unknown."""
    get_source(name)  # raises KeyError if missing
    with _connect() as conn:
        rows = conn.execute(
            """SELECT column_name AS name, column_type AS type,
                      is_primary_key AS primary_key, is_nullable AS nullable,
                      description, source_field, unit, semantic_type
               FROM datasource_columns WHERE table_name=? ORDER BY rowid""",
            (name,),
        ).fetchall()
    return [Column(**r) for r in rows]
