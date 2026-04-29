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
    raw_text: Optional[str] = None


def connect(sqlite_path: str) -> sqlite3.Connection:
    p = Path(sqlite_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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

    # 첨부파일 텍스트 아카이브
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS item_content (
          item_id TEXT NOT NULL,
          source_url TEXT NOT NULL,
          label TEXT,
          mime_type TEXT,
          extracted_text TEXT,
          extraction_status TEXT NOT NULL DEFAULT 'pending',
          extraction_error TEXT,
          fetched_at TEXT,
          PRIMARY KEY (item_id, source_url)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_status ON item_content(extraction_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_item ON item_content(item_id)"
    )

    # FTS5 전문검색 인덱스 (trigram: 한국어 부분문자열 검색)
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
              item_id UNINDEXED,
              title,
              body_text,
              attach_text,
              tokenize='trigram'
            )
            """
        )
    except Exception:
        # trigram 미지원 SQLite (구버전) 폴백
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
              item_id UNINDEXED,
              title,
              body_text,
              attach_text,
              tokenize='unicode61'
            )
            """
        )

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
          last_changed_at, last_sent_at, sent_hash, raw_text
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
                raw_text=r["raw_text"],
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
          last_changed_at, last_sent_at, sent_hash, raw_text
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
                raw_text=r["raw_text"],
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


def mark_old_na_as_sent(conn: sqlite3.Connection, *, tz_name: str) -> int:
    """
    첫 실행 시 이미 DB에 있는 NA 의안(30일 이전)을 발송 완료 처리.
    과거 누적 의안이 한꺼번에 발송되는 것을 방지.
    """
    now = now_iso(tz_name)
    today = now[:10]
    cur = conn.execute(
        """
        UPDATE items
        SET last_sent_at = :now, sent_hash = content_hash
        WHERE source = 'na'
          AND last_sent_at IS NULL
          AND (published_at IS NULL OR published_at < date(:today, '-30 days'))
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


# ── 아카이브 함수 ──────────────────────────────────────────────────────────────


def update_body_text(conn: sqlite3.Connection, *, item_id: str, body_text: str) -> None:
    """items.raw_text 컬럼에 본문 텍스트를 저장합니다."""
    conn.execute(
        "UPDATE items SET raw_text = :body WHERE id = :id AND raw_text IS NULL",
        {"id": item_id, "body": body_text},
    )
    conn.commit()


def upsert_attachment_record(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    source_url: str,
    label: str,
    tz_name: str,
) -> None:
    """첨부파일 URL을 item_content에 등록합니다. 이미 존재하면 무시합니다."""
    now = now_iso(tz_name)
    conn.execute(
        """
        INSERT OR IGNORE INTO item_content (item_id, source_url, label, fetched_at)
        VALUES (:item_id, :source_url, :label, :now)
        """,
        {"item_id": item_id, "source_url": source_url, "label": label, "now": now},
    )
    conn.commit()


def update_attachment_result(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    source_url: str,
    mime_type: Optional[str],
    extracted_text: Optional[str],
    status: str,
    error: Optional[str],
) -> None:
    """첨부파일 추출 결과를 저장합니다."""
    conn.execute(
        """
        UPDATE item_content
        SET mime_type = :mime_type,
            extracted_text = :extracted_text,
            extraction_status = :status,
            extraction_error = :error
        WHERE item_id = :item_id AND source_url = :source_url
        """,
        {
            "item_id": item_id,
            "source_url": source_url,
            "mime_type": mime_type,
            "extracted_text": extracted_text,
            "status": status,
            "error": error,
        },
    )
    conn.commit()


def select_items_missing_body(
    conn: sqlite3.Connection, *, days: int = 30, limit: int = 100
) -> list[sqlite3.Row]:
    """body text(raw_text)가 없는 항목 중 최근 N일 이내 것을 반환합니다."""
    return conn.execute(
        """
        SELECT id, source, url
        FROM items
        WHERE raw_text IS NULL
          AND source != 'scourt'
          AND (published_at IS NULL OR published_at >= date('now', :modifier))
        ORDER BY published_at DESC
        LIMIT :limit
        """,
        {"modifier": f"-{days} days", "limit": limit},
    ).fetchall()


def select_pending_attachments(
    conn: sqlite3.Connection, *, limit: int = 50
) -> list[sqlite3.Row]:
    """extraction_status가 'pending'인 첨부파일 레코드를 반환합니다."""
    return conn.execute(
        """
        SELECT ic.item_id, ic.source_url, ic.label, i.source
        FROM item_content ic
        JOIN items i ON i.id = ic.item_id
        WHERE ic.extraction_status = 'pending'
        ORDER BY i.published_at DESC
        LIMIT :limit
        """,
        {"limit": limit},
    ).fetchall()


def rebuild_fts(conn: sqlite3.Connection) -> int:
    """
    content_fts 인덱스를 전체 재구축합니다.
    body text 또는 첨부파일 텍스트가 있는 항목만 인덱싱합니다.
    Returns: 인덱싱된 항목 수
    """
    conn.execute("DELETE FROM content_fts")
    cur = conn.execute(
        """
        INSERT INTO content_fts (item_id, title, body_text, attach_text)
        SELECT
          i.id,
          i.title,
          COALESCE(i.raw_text, '') AS body_text,
          COALESCE(
            (SELECT GROUP_CONCAT(ic2.extracted_text, '\n')
             FROM item_content ic2
             WHERE ic2.item_id = i.id
               AND ic2.extraction_status = 'success'
               AND ic2.extracted_text IS NOT NULL),
            ''
          ) AS attach_text
        FROM items i
        WHERE i.raw_text IS NOT NULL
           OR EXISTS (
               SELECT 1 FROM item_content ic
               WHERE ic.item_id = i.id AND ic.extraction_status = 'success'
           )
        """
    )
    conn.commit()
    return cur.rowcount


def search_content(
    conn: sqlite3.Connection, *, query: str, limit: int = 20
) -> list[sqlite3.Row]:
    """
    FTS5 인덱스로 전문검색을 수행합니다.
    Returns: 검색 결과 행 목록
    """
    return conn.execute(
        """
        SELECT
          i.id,
          i.source,
          i.title,
          i.url,
          i.published_at,
          i.importance,
          i.summary,
          snippet(content_fts, 2, '[', ']', '...', 24) AS body_snippet,
          snippet(content_fts, 3, '[', ']', '...', 24) AS attach_snippet
        FROM content_fts
        JOIN items i ON i.id = content_fts.item_id
        WHERE content_fts MATCH :query
        ORDER BY rank
        LIMIT :limit
        """,
        {"query": query, "limit": limit},
    ).fetchall()


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

