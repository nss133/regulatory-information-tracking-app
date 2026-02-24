from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from briefing.types import FetchedItem, SourceCode


class SourceConnector(Protocol):
    code: SourceCode

    def fetch_latest(self) -> list[FetchedItem]: ...


@dataclass(frozen=True)
class SourceSpec:
    code: SourceCode
    name_ko: str


SOURCE_SPECS: dict[SourceCode, SourceSpec] = {
    "fsc": SourceSpec(code="fsc", name_ko="금융위원회"),
    "fss": SourceSpec(code="fss", name_ko="금융감독원"),
    "pipc": SourceSpec(code="pipc", name_ko="개인정보보호위원회"),
    "moel": SourceSpec(code="moel", name_ko="고용노동부"),
    "nhrck": SourceSpec(code="nhrck", name_ko="국가인권위원회"),
    "kftc": SourceSpec(code="kftc", name_ko="공정거래위원회"),
    "kofiu": SourceSpec(code="kofiu", name_ko="금융정보분석원"),
    "na": SourceSpec(code="na", name_ko="국회(의안정보시스템)"),
}

