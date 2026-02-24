from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests


@dataclass(frozen=True)
class HttpClient:
    user_agent: str
    timeout_seconds: int = 20

    def get_text(self, url: str, *, params: Optional[dict[str, str]] = None) -> str:
        r = requests.get(
            url,
            params=params,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
        )
        r.raise_for_status()
        # 한국 정부 사이트는 종종 euc-kr 등을 사용하므로 requests의 추정을 존중하되,
        # 없으면 apparent_encoding을 사용합니다.
        if not r.encoding:
            r.encoding = r.apparent_encoding
        return r.text

