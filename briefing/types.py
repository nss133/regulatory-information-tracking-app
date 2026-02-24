from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


SourceCode = Literal["fsc", "fss", "pipc", "moel", "nhrck", "kftc", "kofiu", "na"]
Category = Literal["press", "legislation", "admin_notice", "other"]
Importance = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Attachment:
    label: str
    url: str


@dataclass(frozen=True)
class FetchedItem:
    source: SourceCode
    category: Category
    source_item_key: str
    title: str
    url: str
    published_at: Optional[datetime]
    attachments: list[Attachment] = field(default_factory=list)
    raw_text: Optional[str] = None

