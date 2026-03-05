from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from briefing.types import FetchedItem, Importance
from briefing.utils import content_hash, json_dumps, now_iso


@dataclass(frozen=True)
class StoredItem:
    id: str
    source: str
    category: str
    source_item_key: str
    title: str
    url: str
    published_at: Optional[str]
    attachments_json: str
    content_hash: str
    importance: Optional[Importance]
    importance_reason: Optional[str]
    summary: Optional[str]
    last_changed_at: str
    last_sent_at: Optional[str]
    sent_hash: Optional[str]


def connect(sqlite_path: str) -> sqlite3.Connection:
    p = Path(sqlite_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
          id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          category TEXT NOT NULL,
          source_item_key TEXT NOT NULL,
          title TEXT NOT NULL,
          url TEXT NOT NULL,
          published_at TEXT,
          attachments_json TEXT NOT NULL,
          raw_text TEXT,
          content_hash TEXT NOT NULL,
          summary TEXT,
          importance TEXT,
          importance_reason TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          last_changed_at TEXT NOT NULL,
          sent_hash TEXT,
          last_sent_at TEXT,
          UNIQUE(source, source_item_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_source ON items(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_last_seen ON items(last_seen_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_last_sent ON items(last_sent_at)")
    conn.commit()


def upsert_items(conn: sqlite3.Connection, items: Iterable[FetchedItem], *, tz_name: str) -> int:
    now = now_iso(tz_name)
    count = 0
    for it in items:
        attachments_json = json_dumps([{"label": a.label, "url": a.url} for a in it.attachments])
        published_at = it.published_at.date().isoformat() if isinstance(it.published_at, datetime) else None
        h = content_hash(it.title, it.url, attachments_json)

        # UUID 없이도 안정적으로 upsert하려면, 최초 insert 시 id를 source+key로 두어도 충분합니다.
        # (내부 key이지만 UNIQUE 제약과 동일한 값이므로 충돌/이동에 유리)
        internal_id = f"{it.source}:{it.source_item_key}"

        conn.execute(
            """
            INSERT INTO items (
              id, source, category, source_item_key, title, url,
              published_at, attachments_json, raw_text,
              content_hash, first_seen_at, last_seen_at, last_changed_at
            )
            VALUES (
              :id, :source, :category, :source_item_key, :title, :url,
              :published_at, :attachments_json, :raw_text,
              :content_hash, :now, :now, :now
            )
            ON CONFLICT(source, source_item_key) DO UPDATE SET
              category = excluded.category,
              title = excluded.title,
              url = excluded.url,
              published_at = COALESCE(excluded.published_at, items.published_at),
              attachments_json = excluded.attachments_json,
              raw_text = COALESCE(excluded.raw_text, items.raw_text),
              last_seen_at = excluded.last_seen_at,
              content_hash = excluded.content_hash,
              last_changed_at = CASE
                WHEN items.content_hash != excluded.content_hash THEN excluded.last_changed_at
                ELSE items.last_changed_at
              END
            """,
            {
                "id": internal_id,
                "source": it.source,
                "category": it.category,
                "source_item_key": it.source_item_key,
                "title": it.title,
                "url": it.url,
                "published_at": published_at,
                "attachments_json": attachments_json,
                "raw_text": it.raw_text,
                "content_hash": h,
                "now": now,
            },
        )
        count += 1
    conn.commit()
    return count


def select_pending_for_email(
    conn: sqlite3.Connection, *, max_days_since_published: int = 7
) -> list[StoredItem]:
    # last_sent_at이 없거나, sent_hash != content_hash(변경)면 발송 대상
    # published_at이 N일 이내인 항목만 포함 (오래된 자료 제외). NULL은 포함
    rows = conn.execute(
        """
        SELECT
          id, source, category, source_item_key, title, url,
          published_at, attachments_json, content_hash,
          importance, importance_reason, summary,
          last_changed_at, last_sent_at, sent_hash
        FROM items
        WHERE (last_sent_at IS NULL
           OR sent_hash IS NULL
           OR sent_hash != content_hash)
          AND (published_at IS NULL
               OR published_at >= date('now', :date_modifier))
          AND title NOT LIKE '%은행업감독규정%'
          AND title NOT LIKE '%여신전문금융업%'
          AND title NOT LIKE '%인사발령%'
          AND title NOT LIKE '%인사 발령%'
          AND title NOT LIKE '%인사 보도%'
          AND title NOT LIKE '%대학생기자단%'
          AND title NOT LIKE '%대학생 기자단%'
        ORDER BY
          CASE COALESCE(importance, 'low')
            WHEN 'high' THEN 1
            WHEN 'medium' THEN 2
            WHEN 'low' THEN 3
            ELSE 9
          END,
          COALESCE(published_at, '0000-00-00') DESC,
          CASE source
            WHEN 'fsc' THEN 1
            WHEN 'fss' THEN 2
            WHEN 'na' THEN 3
            WHEN 'kftc' THEN 4
            WHEN 'kofiu' THEN 5
            WHEN 'scourt' THEN 6
            WHEN 'pipc' THEN 7
            WHEN 'moel' THEN 8
            WHEN 'nhrck' THEN 9
            ELSE 999
          END,
          id DESC
        """,
        {"date_modifier": f"-{max_days_since_published} days"},
    ).fetchall()

    out: list[StoredItem] = []
    for r in rows:
        out.append(
            StoredItem(
                id=r["id"],
                source=r["source"],
                category=r["category"],
                source_item_key=r["source_item_key"],
                title=r["title"],
                url=r["url"],
                published_at=r["published_at"],
                attachments_json=r["attachments_json"],
                content_hash=r["content_hash"],
                importance=r["importance"],
                importance_reason=r["importance_reason"],
                summary=r["summary"],
                last_changed_at=r["last_changed_at"],
                last_sent_at=r["last_sent_at"],
                sent_hash=r["sent_hash"],
            )
        )
    return out


def select_last_sent_batch(conn: sqlite3.Connection) -> list[StoredItem]:
    last = conn.execute("SELECT MAX(last_sent_at) AS v FROM items").fetchone()
    last_sent_at = (last["v"] if last else None)
    if not last_sent_at:
        return []

    rows = conn.execute(
        """
        SELECT
          id, source, category, source_item_key, title, url,
          published_at, attachments_json, content_hash,
          importance, importance_reason, summary,
          last_changed_at, last_sent_at, sent_hash
        FROM items
        WHERE last_sent_at = :last_sent_at
          AND title NOT LIKE '%은행업감독규정%'
          AND title NOT LIKE '%여신전문금융업%'
          AND title NOT LIKE '%인사발령%'
          AND title NOT LIKE '%인사 발령%'
          AND title NOT LIKE '%인사 보도%'
          AND title NOT LIKE '%대학생기자단%'
          AND title NOT LIKE '%대학생 기자단%'
        ORDER BY
          CASE COALESCE(importance, 'low')
            WHEN 'high' THEN 1
            WHEN 'medium' THEN 2
            WHEN 'low' THEN 3
            ELSE 9
          END,
          COALESCE(published_at, '0000-00-00') DESC,
          CASE source
            WHEN 'fsc' THEN 1
            WHEN 'fss' THEN 2
            WHEN 'na' THEN 3
            WHEN 'kftc' THEN 4
            WHEN 'pipc' THEN 5
            WHEN 'moel' THEN 6
            WHEN 'nhrck' THEN 7
            ELSE 999
          END,
          id DESC
        """
        ,
        {"last_sent_at": last_sent_at},
    ).fetchall()

    out: list[StoredItem] = []
    for r in rows:
        out.append(
            StoredItem(
                id=r["id"],
                source=r["source"],
                category=r["category"],
                source_item_key=r["source_item_key"],
                title=r["title"],
                url=r["url"],
                published_at=r["published_at"],
                attachments_json=r["attachments_json"],
                content_hash=r["content_hash"],
                importance=r["importance"],
                importance_reason=r["importance_reason"],
                summary=r["summary"],
                last_changed_at=r["last_changed_at"],
                last_sent_at=r["last_sent_at"],
                sent_hash=r["sent_hash"],
            )
        )
    return out


def update_item_enrichment(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    importance: Optional[str],
    importance_reason: Optional[str],
    summary: Optional[str],
) -> None:
    conn.execute(
        """
        UPDATE items
        SET importance = :importance,
            importance_reason = :importance_reason,
            summary = :summary
        WHERE id = :id
        """,
        {
            "id": item_id,
            "importance": importance,
            "importance_reason": importance_reason,
            "summary": summary,
        },
    )
    conn.commit()


def mark_sent(conn: sqlite3.Connection, item_ids: Iterable[str], *, tz_name: str) -> None:
    now = now_iso(tz_name)
    conn.executemany(
        """
        UPDATE items
        SET last_sent_at = :now,
            sent_hash = content_hash
        WHERE id = :id
        """,
        [{"id": i, "now": now} for i in item_ids],
    )
    conn.commit()


def mark_old_kofiu_announce_as_sent(conn: sqlite3.Connection, *, tz_name: str) -> int:
    """
    KoFIU 공고/고시/훈령/예규 중, 오늘 이전에 게시된 미발송 건은
    발송하지 않고 '이미 발송함'으로만 표시한다. 앞으로 올라오는 신규/변경분만 브리핑에 포함.
    """
    now = now_iso(tz_name)
    today = now[:10]  # YYYY-MM-DD
    cur = conn.execute(
        """
        UPDATE items
        SET last_sent_at = :now,
            sent_hash = content_hash
        WHERE source = 'kofiu'
          AND source_item_key LIKE 'announce:%'
          AND last_sent_at IS NULL
          AND (published_at IS NULL OR published_at < :today)
        """,
        {"now": now, "today": today},
    )
    conn.commit()
    return cur.rowcount


def mark_old_scourt_as_sent(conn: sqlite3.Connection, *, tz_name: str) -> int:
    """
    대법원(보도자료/주요판결) 중, 오늘 이전에 게시된 미발송 건은
    발송하지 않고 '이미 발송함'으로만 표시한다.
    앞으로 올라오는 신규/변경분만 브리핑에 포함.
    """
    now = now_iso(tz_name)
    today = now[:10]  # YYYY-MM-DD
    cur = conn.execute(
        """
        UPDATE items
        SET last_sent_at = :now,
            sent_hash = content_hash
        WHERE source = 'scourt'
          AND last_sent_at IS NULL
          AND (published_at IS NULL OR published_at < :today)
        """,
        {"now": now, "today": today},
    )
    conn.commit()
    return cur.rowcount


def reset_sent_after(conn: sqlite3.Connection, after_date: str) -> int:
    """
    지정한 날짜 이후로 발송 처리된 항목을 미발송 상태로 되돌립니다.
    after_date: 'YYYY-MM-DD'. last_sent_at이 이 날짜 이상인 행의 last_sent_at, sent_hash를 NULL로.
    """
    cur = conn.execute(
        """
        UPDATE items
        SET last_sent_at = NULL,
            sent_hash = NULL
        WHERE date(last_sent_at) >= date(:after_date)
        """,
        {"after_date": after_date},
    )
    conn.commit()
    return cur.rowcount

