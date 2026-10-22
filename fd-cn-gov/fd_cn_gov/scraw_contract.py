"""Stable contract for scraw scraper scripts (vendored, monorepo-decoupled).

Every scraper ships a module-level `MANIFEST = ScrawManifest(...)`. The manifest
is the single source of truth for the script's identity, crawl recipe, and output
record schema. `build_registry.py` consumes it to write the bundled `registry.db`;
the read API (`fd_cn_gov.get_source` / `get_columns`) serves the same shape.

Stdlib only (dataclasses + json) — no extra dependency.

This is a trimmed copy of `mcp/scrapling-uv-mcp/scripts/scraw_contract.py`:
`to_config()` drops the monorepo-only `scraper_script` / `scraper_script_docker`
path fields (meaningless outside the monorepo). `to_columns_json()` and
`to_scraw_columns()` — the parts `build_registry` uses — are byte-identical to
the monorepo so the registry rows match `daas.db` for these sources.

Self-check: `python -m fd_cn_gov.scraw_contract` runs a round-trip assert.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict


@dataclass
class ScrawColumn:
    """One column in the scraper's output records.

    `source_field` is the "scraw bridge" — the CSS selector, URL token, or
    derived path that produced this column. Keep it specific (`a.news-link@href`,
    `url:re:t(\\d{8})_`, `meta:section`) so the column is self-documenting.
    """
    name: str
    type: str = "string"          # string|integer|float|date|datetime|boolean
    primary_key: bool = False
    nullable: bool = True
    description: str = ""
    source_field: str = ""
    unit: str = ""
    semantic_type: str = ""       # title|date|url|identifier|category|amount|text


@dataclass
class ScrawArchive:
    """One paginated archive / sub-section URL the scraper crawls."""
    section: str
    subsection: str
    url: str


@dataclass
class ScrawManifest:
    """Identity + crawl recipe + output schema for one scraper script.

    `name` must equal the script's basename (without `.py`) — `build_registry`
    uses it to label `datasource_columns.table_name`.
    """
    name: str
    label: str
    url: str                              # seed URL (the user's input)
    description: str = ""
    category: str = "网页抓取"
    category_label: str = "Web Scraw"
    columns: list[ScrawColumn] = field(default_factory=list)
    archives: list[ScrawArchive] = field(default_factory=list)
    crawl: dict = field(default_factory=dict)  # free-form: selectors, pagination, caps

    def to_columns_json(self) -> list[dict]:
        """N rows for the `datasource_columns` table (one per column).

        Translates idiomatic field names to the on-disk shape:
        - `name` → `column_name`, `type` → `column_type`
        - `primary_key`/`nullable` bools → `is_primary_key`/`is_nullable` 0/1
        - `table_name` = manifest.name (logical table)
        """
        return [
            {
                "table_name": self.name,
                "column_name": c.name,
                "column_type": c.type,
                "is_primary_key": 1 if c.primary_key else 0,
                "is_nullable": 1 if c.nullable else 0,
                "description": c.description,
                "source_field": c.source_field,
                "unit": c.unit,
                "semantic_type": c.semantic_type,
            }
            for c in self.columns
        ]

    def to_scraw_columns(self) -> list[dict]:
        """Simple [{name, type, description}] list for `scraw_configs.columns_json`."""
        return [
            {"name": c.name, "type": c.type, "description": c.description}
            for c in self.columns
        ]

    def to_config(self) -> dict:
        """Self-describing blob. Trimmed vs. the monorepo: no monorepo-path keys."""
        return {
            "type": "scraw",
            "seed_url": self.url,
            "category": {"name": self.category, "label": self.category_label},
            "crawl": self.crawl,
            "archives": [asdict(a) for a in self.archives],
            "columns": self.to_columns_json(),
        }

    def to_config_json(self) -> str:
        return json.dumps(self.to_config(), ensure_ascii=False)


def _selfcheck() -> None:
    """Round-trip assert. Run with `python -m fd_cn_gov.scraw_contract`."""
    m = ScrawManifest(
        name="demo_src",
        label="Demo",
        url="https://example.com/list",
        description="tiny demo",
        columns=[
            ScrawColumn(name="title", type="string", nullable=False,
                        description="document title", source_field="span.title",
                        semantic_type="title"),
            ScrawColumn(name="url", type="string", primary_key=True, nullable=False,
                        description="canonical url", source_field="a@href",
                        semantic_type="url"),
        ],
        archives=[ScrawArchive(section="Main", subsection="News",
                               url="https://example.com/list")],
        crawl={"item_selector": "li.news-item", "default_max_pages": 1},
    )
    cols = m.to_columns_json()
    assert len(cols) == 2 and cols[0]["column_name"] == "title", cols
    assert cols[1]["is_primary_key"] == 1 and cols[1]["is_nullable"] == 0, cols[1]
    assert cols[0]["table_name"] == "demo_src", cols[0]
    scraw = m.to_scraw_columns()
    assert scraw == [{"name": "title", "type": "string", "description": "document title"},
                     {"name": "url", "type": "string", "description": "canonical url"}], scraw
    blob = json.loads(m.to_config_json())
    assert blob["columns"] == cols, "config.columns must match datasource_columns rows"
    assert blob["archives"][0]["section"] == "Main", blob["archives"]
    assert blob["type"] == "scraw", blob
    assert "scraper_script" not in blob, "monorepo-path key must be dropped"
    print("fd_cn_gov.scraw_contract self-check OK")


if __name__ == "__main__":
    _selfcheck()
