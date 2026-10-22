"""Browser session utilities — local by default, remote via env."""

import os

from scrapling.fetchers import AsyncDynamicSession


def get_browser_session() -> AsyncDynamicSession:
    """Return a browser session.

    Remote if BROWSER_CDP_URL is set (e.g., ws://browser-cdp:3000), else local headless.

    ponytail: lazy routing — zero config = local, one env var = remote k8s browser
    """
    cdp_url = os.getenv("BROWSER_CDP_URL")
    if cdp_url:
        return AsyncDynamicSession(cdp_url=cdp_url, network_idle=True)
    return AsyncDynamicSession(headless=True, network_idle=True)
