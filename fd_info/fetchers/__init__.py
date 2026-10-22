"""信源注册表：启停与按名取 fetcher。"""

from __future__ import annotations

from .base import Source
from .cninfo import CninfoSource
from .interaction import InteractionSource
from .ministry import MinistrySource

SOURCES: dict[str, type[Source]] = {
    CninfoSource.name: CninfoSource,
    MinistrySource.name: MinistrySource,
    InteractionSource.name: InteractionSource,
}

#: 默认启用：公告与部委。互动待上游恢复后加入。
DEFAULT_ENABLED: tuple[str, ...] = (CninfoSource.name, MinistrySource.name)


def enabled_sources(names: list[str] | None = None) -> list[Source]:
    chosen = names or list(DEFAULT_ENABLED)
    return [SOURCES[name]() for name in chosen if name in SOURCES]
