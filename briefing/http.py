from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from typing import Optional

import requests
import urllib3

# 한국 정부 사이트는 SSL 인증서 검증 문제가 있는 경우가 있어 verify=False 사용
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass(frozen=True)
class HttpClient:
    user_agent: str
    timeout_seconds: int = 20

    def get_text(self, url: str, *, params: Optional[dict[str, str]] = None) -> str:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        _RETRYABLE_STATUS = {429, 500, 502, 503, 504}
        last_exc: Exception = RuntimeError("unreachable")
        for attempt in range(3):
            try:
                r = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    verify=False,
                )
                if r.status_code in _RETRYABLE_STATUS and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                # 한국 정부 사이트는 종종 euc-kr 등을 사용하므로 requests의 추정을 존중하되,
                # 없으면 apparent_encoding을 사용합니다.
                if not r.encoding:
                    r.encoding = r.apparent_encoding
                return r.text
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(2 ** attempt)  # 1s, 2s
        raise last_exc

