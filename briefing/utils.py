from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dateutil import tz


def now_iso(tz_name: str) -> str:
    z = tz.gettz(tz_name)
    return datetime.now(tz=z).isoformat()


def parse_yyyy_mm_dd(s: str) -> Optional[datetime]:
    s = s.strip()
    m = re.search(r"(20\d{2})[-.](\d{2})[-.](\d{2})", s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return datetime(y, mo, d)


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def normalize_url_for_hash(url: str) -> str:
    """쿼리 파라미터 순서 차이로 인한 잘못된 '변경' 감지 방지."""
    try:
        parsed = urlparse(url)
        if parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=True)
            sorted_query = urlencode(sorted(qs.items()), doseq=True)
            parsed = parsed._replace(query=sorted_query)
        return urlunparse(parsed)
    except Exception:
        return url


def content_hash(title: str, url: str, attachments_json: str) -> str:
    h = hashlib.sha256()
    h.update(normalize_ws(title).encode("utf-8"))
    h.update(b"\n")
    h.update(normalize_url_for_hash(url).encode("utf-8"))
    h.update(b"\n")
    h.update(attachments_json.encode("utf-8"))
    return h.hexdigest()

