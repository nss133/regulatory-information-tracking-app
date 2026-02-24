from __future__ import annotations

from bs4 import BeautifulSoup


def soupify(html: str) -> BeautifulSoup:
    # lxml이 설치돼 있으면 lxml 파서를 사용하고, 아니면 내장 html.parser로 폴백합니다.
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")

