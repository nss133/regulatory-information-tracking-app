from __future__ import annotations

from briefing.config import FetchConfig
from briefing.http import HttpClient
from briefing.sources.fsc import FscConnector
from briefing.sources.fss import FssConnector
from briefing.sources.kofiu import KofiuConnector
from briefing.sources.kftc import KftcConnector
from briefing.sources.moel import MoelConnector
from briefing.sources.nhrck import NhrckConnector
from briefing.sources.na import NaAssemblyConnector
from briefing.sources.pipc import PipcConnector
from briefing.sources.scourt import ScourtConnector
from briefing.sources.registry import SourceConnector


def build_connectors(fetch: FetchConfig) -> list[SourceConnector]:
    http = HttpClient(user_agent=fetch.user_agent, timeout_seconds=fetch.request_timeout_seconds)
    max_items = fetch.max_items_per_source
    return [
        FscConnector(http, max_items=max_items),
        FssConnector(http, max_items=max_items),
        KofiuConnector(http, max_items=max_items),
        ScourtConnector(http, max_items=max_items),
        PipcConnector(http, max_items=max_items),
        MoelConnector(http, max_items=max_items),
        NhrckConnector(http, max_items=max_items),
        KftcConnector(http, max_items=max_items),
        NaAssemblyConnector(http, max_items=max_items),
    ]

