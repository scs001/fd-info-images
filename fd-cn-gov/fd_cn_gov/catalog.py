"""fd-cn-gov datasource manifest conforming to fd-open-data-protocol.

This module exposes a DatasourceManifest-compatible CATALOG for Chinese
central-government ministry open-information archives (11 ministries).
Loadable via: load_catalog("fd_cn_gov.catalog:CATALOG")

Single source of truth: the ``functions`` list is **derived** from the 11
scraper scripts' ``MANIFEST`` (the same source ``build_registry`` uses for
``registry.db``). Hand-written command blocks drifted once already (a prior
revision advertised 4 commands with no script behind them and missed 9 real
sources) — deriving removes the failure mode structurally.

Note on imports: deriving ``functions`` pulls in the 11 scraper modules (and
through them ``scrapling``), which ``pyproject.toml`` already declares as a
runtime dependency. The protocol's ``load_catalog`` reads ``CATALOG`` at import
time anyway, so there is no laziness to preserve.
"""
from __future__ import annotations

import importlib

#: The 11 ministry scrapers, alphabetical by module name (deterministic order).
#: Mirrors ``fd_cn_gov.build_registry.SCRIPT_NAMES`` — kept as a local literal
#: rather than an import to avoid a circular dependency (build_registry reads
#: the scripts; nothing here should depend on build_registry).
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


def _load_manifests() -> list:
    """Import each script module and return its MANIFEST (name-checked)."""
    manifests = []
    for name in SCRIPT_NAMES:
        mod = importlib.import_module(f"fd_cn_gov.scripts.{name}")
        m = getattr(mod, "MANIFEST", None)
        if m is None or m.name != name:
            raise RuntimeError(f"{name}: MANIFEST missing or name mismatch")
        manifests.append(m)
    return manifests


def _functions_from_manifests() -> list[dict]:
    """Derive protocol ``functions`` entries from the scripts' MANIFESTs.

    Each entry carries the command (script) name, its description, and its
    output column schema — all from the same MANIFEST that feeds
    ``registry.db``, so the two surfaces cannot drift apart again.
    """
    functions = []
    for m in _load_manifests():
        functions.append(
            {
                "command": m.name,
                "category": m.category,
                "description": m.description,
                "frequency": "daily",
                "parameters": [
                    {
                        "name": "max_pages",
                        "type": "int",
                        "required": False,
                        "description": (
                            "pages per archive (0 = full crawl); default is the "
                            "script's own cap"
                        ),
                    }
                ],
                "columns": [
                    {
                        "name": c.name,
                        "type": c.type,
                        "description": c.description,
                    }
                    for c in m.columns
                ],
            }
        )
    return functions


CATALOG = {
    "version": "1",
    "name": "fd-cn-gov",
    "label": "Chinese Central Government Archives",
    "source_url": "https://github.com/FindDataTechnology/finddata/tree/main/fd-cn-gov",
    "ranking_seed": [0.85, 0.7],
    "scanner_mode": "full",
    # Derived from the 11 scripts' MANIFESTs (single source of truth).
    "functions": _functions_from_manifests(),
    "concepts": [
        # Government document concepts. Every column referenced here exists in
        # at least one MANIFEST (the two former taxonomy_* concept hints
        # pointed at columns no scraper emits and were dropped).
        {"column": "title", "concept": "document.title", "entity_type": "organization", "measure": "official_document", "unit": "string", "frequency": "variable"},
        {"column": "date", "concept": "document.publish_date", "entity_type": "organization", "measure": "publication_date", "unit": "date", "frequency": "variable"},
        {"column": "url", "concept": "document.url", "entity_type": "organization", "measure": "document_url", "unit": "url", "frequency": "variable"},
        {"column": "doc_type", "concept": "document.format", "entity_type": "organization", "measure": "format_type", "unit": "categorical", "frequency": "variable"},
    ],
    "entities": [
        {"entity_type": "organization", "coverage": "universe"},
        {"entity_type": "country", "coverage": "explicit", "codes": ["CN"]},
    ],
    "fetch": {"runner": "fd-cn-gov"},
}


# ---------------------------------------------------------------- provider
#
# Dispatch shapes across the 11 scripts (see change design D2):
#   A. multi-archive tuple tables → crawl_archive(section, subsection, url, max_pages)
#      mee_gsgg / mnr_tzgg / mof_gkml / mohurd_xinwen / mot_shuju / ndrc_tzgg / safe_whxw
#   B. single-archive               → crawl(max_pages)
#      mem_tzgg / pbc_xinwen
#   C. landing-only, no paging      → crawl()
#      mofcom_xwfb
#   D. category walk                → list_categories() + crawl_category(nid, label)
#      moa_govpublic
#
# The ``max_pages`` parameter mirrors the CLI semantics: 0 = full crawl
# (--all); omitted = each script's own argparse default.

#: Default page caps per script (mirrors each script's argparse default).
_DEFAULT_MAX_PAGES = {
    "mee_gsgg_archive": 50,
    "mem_tzgg_archive": 50,
    "mnr_tzgg_archive": 50,
    "moa_govpublic_archive": 0,  # full walk of 24 categories
    "mof_gkml_archive": 50,
    "mofcom_xwfb_archive": 1,
    "mohurd_xinwen_archive": 50,
    "mot_shuju_archive": 1,
    "ndrc_tzgg_archive": 50,
    "pbc_xinwen_archive": 50,
    "safe_whxw_archive": 50,
}

#: Shape per script (see table above).
_SHAPES = {
    "mee_gsgg_archive": "A",
    "mem_tzgg_archive": "B",
    "mnr_tzgg_archive": "A",
    "moa_govpublic_archive": "D",
    "mof_gkml_archive": "A",
    "mofcom_xwfb_archive": "C",
    "mohurd_xinwen_archive": "A",
    "mot_shuju_archive": "A",
    "ndrc_tzgg_archive": "A",
    "pbc_xinwen_archive": "B",
    "safe_whxw_archive": "A",
}


def _dispatch(command: str, params: dict) -> list[dict]:
    """Dispatch one command to its scraper's crawl entry (real fetch code).

    Scrapers print their records to stdout in ``main()``; the callable entries
    (``crawl_archive`` / ``crawl`` / ``crawl_category``) return record lists
    directly, which is what a programmatic caller wants.
    """
    shape = _SHAPES.get(command)
    if shape is None:
        raise NotImplementedError(
            f"Unknown command: {command!r}. "
            f"Available: {', '.join(SCRIPT_NAMES)}. "
            f"Alternatively use the CLI: fd-cn-gov crawl <name> [--max-pages N|--all]"
        )
    max_pages = params.get("max_pages", _DEFAULT_MAX_PAGES[command])
    mod = importlib.import_module(f"fd_cn_gov.scripts.{command}")

    if shape == "A":
        records: list[dict] = []
        for entry in mod.ARCHIVES:
            section, subsection, url = entry
            records.extend(mod.crawl_archive(section, subsection, url, max_pages))
        return records
    if shape == "B":
        return mod.crawl(max_pages)
    if shape == "C":
        return mod.crawl()
    # shape == "D"
    records = []
    for nid, label in mod.list_categories():
        records.extend(mod.crawl_category(nid, label))
    return records


class FDcnGovProvider:
    """DataProvider class for fd-open-data-protocol compatibility."""

    name = "fd-cn-gov"

    def registry(self) -> dict:
        """Return the CATALOG."""
        return CATALOG

    def run(self, command: str, params: dict):
        """Execute one registered scraper and return its records.

        Args:
            command: scraper name (``SCRIPT_NAMES``), e.g. ``"mof_gkml_archive"``.
            params: command parameters as keyword values; only ``max_pages``
                is consumed (0 = full crawl, omitted = the script's default).

        Returns:
            List of dicts (one per listed document), schema per MANIFEST.

        Raises:
            NotImplementedError: If the command is not a registered scraper.
                Explicit raise (not a dangling import) so "unimplemented"
                cannot masquerade as "broken implementation".
        """
        return _dispatch(command, params)
