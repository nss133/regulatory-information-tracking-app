from __future__ import annotations

import sqlite3
import uuid
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
    relevance_score: Optional[int] = None
    relevance_evidence: Optional[str] = None
    deadlines_json: Optional[str] = None


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

    # 소스가 정상 응답했지만 게시물이 0건인 경우와 실제 수집 실패를 구분한다.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_health (
          source TEXT PRIMARY KEY,
          last_attempt_at TEXT NOT NULL,
          last_success_at TEXT,
          last_item_count INTEGER NOT NULL DEFAULT 0,
          last_duration_seconds REAL,
          last_error TEXT,
          consecutive_failures INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # 기능 도입 전부터 데이터가 있던 소스는 마지막 관측 시각으로 초기 상태를 채운다.
    # 이후 실제 수집 시 record_source_fetches가 정확한 실행 결과로 갱신한다.
    conn.execute(
        """
        INSERT OR IGNORE INTO source_health (
          source, last_attempt_at, last_success_at, last_item_count,
          last_duration_seconds, last_error, consecutive_failures
        )
        SELECT i.source, MAX(i.last_seen_at), MAX(i.last_seen_at), COUNT(*),
               NULL, NULL, 0
        FROM items i
        GROUP BY i.source
        """
    )

    # 미래에셋생명 관련성 컬럼 — 중요도와 별도로 정렬과 ★ 표시에 사용.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    if "relevance_score" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN relevance_score INTEGER")
    if "relevance_evidence" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN relevance_evidence TEXT")
    # 메일 편집 정책상 제외된 항목은 '발송'과 구분해 기록한다. 같은 content_hash는
    # 다음 실행에서 다시 후보가 되지 않지만, 제목/첨부가 바뀌면 자동으로 재평가된다.
    if "suppressed_hash" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN suppressed_hash TEXT")
    if "last_suppressed_at" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN last_suppressed_at TEXT")
    if "deadlines_json" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN deadlines_json TEXT")

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

    # 법률 태그는 importance·relevance·발송 상태와 분리된 shadow 계층이다.
    # 현재 profile의 결과만 item_tags에 두고, 사람 판정은 tag_reviews에 이력으로 보존한다.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_registry (
          tag_id TEXT PRIMARY KEY,
          canonical TEXT NOT NULL,
          hashtag TEXT NOT NULL,
          tag_type TEXT NOT NULL,
          tag_type_label TEXT,
          aliases_json TEXT NOT NULL DEFAULT '[]',
          producer_package TEXT NOT NULL,
          producer_version TEXT NOT NULL,
          producer_commit TEXT NOT NULL,
          profile_version TEXT NOT NULL,
          taxonomy_sha256 TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          UNIQUE(tag_type, canonical)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_registry_hashtag ON tag_registry(hashtag)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_registry_type ON tag_registry(tag_type)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS item_tag_state (
          item_id TEXT PRIMARY KEY,
          input_hash TEXT NOT NULL,
          profile_version TEXT NOT NULL,
          taxonomy_sha256 TEXT NOT NULL,
          status TEXT NOT NULL,
          tag_count INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          tagged_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_item_tag_state_status ON item_tag_state(status)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS item_tags (
          item_id TEXT NOT NULL,
          tag_id TEXT NOT NULL,
          score INTEGER NOT NULL,
          confidence TEXT,
          candidate_status TEXT NOT NULL,
          source_fields_json TEXT NOT NULL DEFAULT '[]',
          evidence_json TEXT NOT NULL DEFAULT '[]',
          aliases_json TEXT NOT NULL DEFAULT '[]',
          occurrences INTEGER NOT NULL DEFAULT 0,
          strict_match INTEGER NOT NULL DEFAULT 0,
          profile_version TEXT NOT NULL,
          taxonomy_sha256 TEXT NOT NULL,
          input_hash TEXT NOT NULL,
          tagged_at TEXT NOT NULL,
          PRIMARY KEY (item_id, tag_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id, score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_item ON item_tags(item_id, score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_item_tags_review ON item_tags(candidate_status, confidence)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS item_tag_relations (
          item_id TEXT NOT NULL,
          source_tag_id TEXT NOT NULL,
          target_tag_id TEXT NOT NULL,
          relation_type TEXT NOT NULL,
          score INTEGER NOT NULL,
          evidence_text TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'candidate',
          profile_version TEXT NOT NULL,
          taxonomy_sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (
            item_id, source_tag_id, target_tag_id, relation_type, evidence_text
          )
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_item_tag_relations_item ON item_tag_relations(item_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_reviews (
          review_id INTEGER PRIMARY KEY AUTOINCREMENT,
          item_id TEXT NOT NULL,
          tag_id TEXT,
          decision TEXT NOT NULL CHECK(decision IN ('accept','reject','edit','missed')),
          replacement_tag_id TEXT,
          reason_code TEXT,
          note TEXT,
          reviewer TEXT NOT NULL,
          reviewed_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_reviews_item ON tag_reviews(item_id, tag_id, review_id DESC)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tagging_runs (
          run_id TEXT PRIMARY KEY,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          status TEXT NOT NULL,
          mode TEXT NOT NULL,
          selected_count INTEGER NOT NULL DEFAULT 0,
          processed_count INTEGER NOT NULL DEFAULT 0,
          tagged_item_count INTEGER NOT NULL DEFAULT 0,
          tag_count INTEGER NOT NULL DEFAULT 0,
          error_count INTEGER NOT NULL DEFAULT 0,
          profile_version TEXT NOT NULL,
          taxonomy_sha256 TEXT NOT NULL,
          producer_commit TEXT NOT NULL,
          error TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tagging_runs_started ON tagging_runs(started_at)")

    # 규제동향과 기존 판단 근거의 겹침을 사람 검수 가능한 구조화 큐로 보존한다.
    # exact_article/exact_case만 개별 J-ID를 가리키며 law_only는 광역 후보로 분리한다.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jid_impact_candidates (
          candidate_id TEXT PRIMARY KEY,
          item_id TEXT NOT NULL,
          item_content_hash TEXT NOT NULL,
          item_title TEXT NOT NULL,
          item_url TEXT,
          match_level TEXT NOT NULL CHECK(
            match_level IN ('exact_article', 'exact_case', 'law_only')
          ),
          priority INTEGER NOT NULL,
          law_name TEXT,
          article TEXT,
          case_number TEXT,
          jid TEXT,
          jid_stem TEXT,
          related_jid_count INTEGER NOT NULL DEFAULT 0,
          evidence_text TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(
            status IN ('unreviewed', 'no_effect', 'affected', 'deferred')
          ),
          first_detected_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          detection_count INTEGER NOT NULL DEFAULT 1,
          source_run_id TEXT,
          reviewed_at TEXT,
          reviewer TEXT,
          review_reason TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jid_impact_queue "
        "ON jid_impact_candidates(status, priority, last_seen_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jid_impact_jid "
        "ON jid_impact_candidates(jid, status)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jid_impact_reviews (
          review_id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id TEXT NOT NULL,
          decision TEXT NOT NULL CHECK(
            decision IN ('unreviewed', 'no_effect', 'affected', 'deferred')
          ),
          reason TEXT NOT NULL,
          reviewer TEXT NOT NULL,
          reviewed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jid_impact_reviews_candidate "
        "ON jid_impact_reviews(candidate_id, review_id DESC)"
    )

    # 한 번의 자동 실행·메일 발송을 배치 단위로 추적한다. SMTP 접수와 items의
    # 발송 표시 사이에서 프로세스가 중단돼도 모호한 상태를 보존해 중복을 막는다.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_runs (
          run_id TEXT PRIMARY KEY,
          run_date TEXT NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          status TEXT NOT NULL,
          stage TEXT NOT NULL,
          fetched_count INTEGER NOT NULL DEFAULT 0,
          candidate_count INTEGER NOT NULL DEFAULT 0,
          visible_count INTEGER NOT NULL DEFAULT 0,
          suppressed_count INTEGER NOT NULL DEFAULT 0,
          error_count INTEGER NOT NULL DEFAULT 0,
          subject TEXT,
          recipients_json TEXT,
          message_id TEXT,
          delivery_fingerprint TEXT,
          archive_path TEXT,
          duration_seconds REAL,
          error TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_delivery_runs_started "
        "ON delivery_runs(started_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_delivery_runs_fingerprint "
        "ON delivery_runs(delivery_fingerprint, status)"
    )
    delivery_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(delivery_runs)")
    }
    if "duration_seconds" not in delivery_cols:
        conn.execute("ALTER TABLE delivery_runs ADD COLUMN duration_seconds REAL")
    # 기능 도입 전 마지막 발송은 확인 가능한 시각·건수만 1회 이관한다.
    # 당시 Message-ID와 HTML은 저장되지 않았으므로 추정값을 만들지 않는다.
    conn.execute(
        """
        INSERT OR IGNORE INTO delivery_runs (
          run_id, run_date, started_at, completed_at, status, stage,
          candidate_count, visible_count
        )
        SELECT 'legacy-' || REPLACE(REPLACE(MAX(last_sent_at), ':', ''), '+', '_'),
               substr(MAX(last_sent_at), 1, 10), MAX(last_sent_at), MAX(last_sent_at),
               'completed', 'legacy_import',
               COUNT(*), COUNT(*)
        FROM items
        WHERE last_sent_at = (SELECT MAX(last_sent_at) FROM items)
          AND NOT EXISTS (SELECT 1 FROM delivery_runs)
        HAVING MAX(last_sent_at) IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_watch_alerts (
          alert_date TEXT PRIMARY KEY,
          detected_at TEXT NOT NULL,
          reason_code TEXT NOT NULL,
          reason TEXT NOT NULL,
          run_id TEXT,
          alerted_at TEXT NOT NULL,
          message_id TEXT
        )
        """
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


class AmbiguousDeliveryError(RuntimeError):
    """동일 내용의 과거 SMTP 처리 결과가 불명확해 자동 재발송을 막았음."""


def create_delivery_run(conn: sqlite3.Connection, *, tz_name: str) -> str:
    run_id = uuid.uuid4().hex
    now = now_iso(tz_name)
    conn.execute(
        """
        INSERT INTO delivery_runs (
          run_id, run_date, started_at, status, stage
        ) VALUES (?, ?, ?, 'running', 'started')
        """,
        (run_id, now[:10], now),
    )
    conn.commit()
    return run_id


def update_delivery_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    completed: bool = False,
    tz_name: str | None = None,
    **fields,
) -> None:
    allowed = {
        "fetched_count", "candidate_count", "visible_count", "suppressed_count",
        "error_count", "subject", "recipients_json", "message_id",
        "delivery_fingerprint", "archive_path", "error",
        "duration_seconds",
    }
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"허용되지 않은 delivery_runs 필드: {sorted(invalid)}")
    values = dict(fields)
    if status is not None:
        values["status"] = status
    if stage is not None:
        values["stage"] = stage
    if completed:
        if not tz_name:
            raise ValueError("completed=True이면 tz_name이 필요합니다.")
        values["completed_at"] = now_iso(tz_name)
    if not values:
        return
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    values["run_id"] = run_id
    conn.execute(
        f"UPDATE delivery_runs SET {assignments} WHERE run_id = :run_id", values
    )
    conn.commit()


def prepare_delivery_attempt(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    fingerprint: str,
    message_id: str,
) -> None:
    """SMTP 직전 상태를 원자적으로 기록하고 동일한 모호 배치를 차단한다."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        prior = conn.execute(
            """
            SELECT run_id, started_at, status
            FROM delivery_runs
            WHERE delivery_fingerprint = ?
              AND run_id != ?
              AND status IN ('sending', 'smtp_accepted', 'ambiguous')
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (fingerprint, run_id),
        ).fetchone()
        if prior:
            raise AmbiguousDeliveryError(
                "동일 브리핑의 과거 발송 결과를 확인해야 합니다: "
                f"run_id={prior['run_id']}, status={prior['status']}, "
                f"started_at={prior['started_at']}"
            )
        conn.execute(
            """
            UPDATE delivery_runs
            SET status = 'sending', stage = 'smtp',
                delivery_fingerprint = ?, message_id = ?
            WHERE run_id = ?
            """,
            (fingerprint, message_id, run_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def select_recent_delivery_runs(
    conn: sqlite3.Connection, *, limit: int = 10
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT run_id, run_date, started_at, completed_at, status, stage,
               fetched_count, candidate_count, visible_count, suppressed_count,
               error_count, subject, message_id, archive_path,
               duration_seconds, error
        FROM delivery_runs
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (max(1, min(limit, 100)),),
    ).fetchall()


def has_delivery_watch_alert(conn: sqlite3.Connection, alert_date: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM delivery_watch_alerts WHERE alert_date = ?", (alert_date,)
    ).fetchone() is not None


def record_delivery_watch_alert(
    conn: sqlite3.Connection,
    *,
    alert_date: str,
    detected_at: str,
    reason_code: str,
    reason: str,
    run_id: str | None,
    message_id: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO delivery_watch_alerts (
          alert_date, detected_at, reason_code, reason, run_id,
          alerted_at, message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_date, detected_at, reason_code, reason, run_id,
            detected_at, message_id,
        ),
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
    # 현재 버전(content_hash)이 발송되거나 편집 정책상 제외된 적이 없으면 후보.
    # 이미 제외된 항목도 제목/첨부가 바뀌면 hash가 달라져 다시 평가된다.
    # published_at이 N일 이내인 항목만 포함 (오래된 자료 제외). NULL은 포함
    rows = conn.execute(
        """
        SELECT
          id, source, category, source_item_key, title, url,
          published_at, attachments_json, content_hash,
          importance, importance_reason, summary,
          last_changed_at, last_sent_at, sent_hash, raw_text,
          relevance_score, relevance_evidence
          , deadlines_json
        FROM items
        WHERE content_hash != COALESCE(sent_hash, '')
          AND content_hash != COALESCE(suppressed_hash, '')
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
                relevance_score=r["relevance_score"],
                relevance_evidence=r["relevance_evidence"],
                deadlines_json=r["deadlines_json"],
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
          last_changed_at, last_sent_at, sent_hash, raw_text,
          relevance_score, relevance_evidence
          , deadlines_json
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
                relevance_score=r["relevance_score"],
                relevance_evidence=r["relevance_evidence"],
                deadlines_json=r["deadlines_json"],
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
    deadlines_json: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE items
        SET importance = :importance,
            importance_reason = :importance_reason,
            summary = :summary,
            deadlines_json = :deadlines_json
        WHERE id = :id
        """,
        {
            "id": item_id,
            "importance": importance,
            "importance_reason": importance_reason,
            "summary": summary,
            "deadlines_json": deadlines_json,
        },
    )
    conn.commit()


def update_item_relevance(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    relevance_score: int,
    relevance_evidence: Optional[str],
) -> None:
    conn.execute(
        """
        UPDATE items
        SET relevance_score = :score,
            relevance_evidence = :evidence
        WHERE id = :id
        """,
        {"id": item_id, "score": relevance_score, "evidence": relevance_evidence},
    )
    conn.commit()


def mark_sent(conn: sqlite3.Connection, item_ids: Iterable[str], *, tz_name: str) -> None:
    now = now_iso(tz_name)
    conn.executemany(
        """
        UPDATE items
        SET last_sent_at = :now,
            sent_hash = content_hash,
            last_suppressed_at = NULL,
            suppressed_hash = NULL
        WHERE id = :id
        """,
        [{"id": i, "now": now} for i in item_ids],
    )
    conn.commit()


def mark_suppressed(conn: sqlite3.Connection, item_ids: Iterable[str], *, tz_name: str) -> None:
    """메일 편집 정책으로 노출하지 않은 현재 버전을 별도 상태로 기록한다."""
    now = now_iso(tz_name)
    conn.executemany(
        """
        UPDATE items
        SET last_suppressed_at = :now,
            suppressed_hash = content_hash
        WHERE id = :id
        """,
        [{"id": i, "now": now} for i in item_ids],
    )
    conn.commit()


def record_source_fetches(
    conn: sqlite3.Connection,
    outcomes: dict[str, tuple[int, Optional[str], float]],
    *,
    tz_name: str,
) -> None:
    """소스별 최신 접속 결과를 기록한다. 0건 성공은 실패가 아니다."""
    now = now_iso(tz_name)
    for source, (item_count, error, duration) in outcomes.items():
        conn.execute(
            """
            INSERT INTO source_health (
              source, last_attempt_at, last_success_at, last_item_count,
              last_duration_seconds, last_error, consecutive_failures
            ) VALUES (
              :source, :now, :success_at, :item_count,
              :duration, :error, :failures
            )
            ON CONFLICT(source) DO UPDATE SET
              last_attempt_at = excluded.last_attempt_at,
              last_success_at = CASE
                WHEN excluded.last_error IS NULL THEN excluded.last_success_at
                ELSE source_health.last_success_at
              END,
              last_item_count = excluded.last_item_count,
              last_duration_seconds = excluded.last_duration_seconds,
              last_error = excluded.last_error,
              consecutive_failures = CASE
                WHEN excluded.last_error IS NULL THEN 0
                ELSE source_health.consecutive_failures + 1
              END
            """,
            {
                "source": source,
                "now": now,
                "success_at": now if error is None else None,
                "item_count": item_count,
                "duration": duration,
                "error": error,
                "failures": 0 if error is None else 1,
            },
        )
    conn.commit()


def mark_old_kofiu_announce_as_sent(conn: sqlite3.Connection, *, tz_name: str) -> int:
    """
    KoFIU 커넥터 최초 활성화 시 오늘 이전의 누적 보도자료·법령자료는
    발송하지 않고 기준선으로 표시한다. 이후 신규/변경분만 브리핑에 포함한다.
    """
    now = now_iso(tz_name)
    today = now[:10]  # YYYY-MM-DD
    cur = conn.execute(
        """
        UPDATE items
        SET last_sent_at = :now,
            sent_hash = content_hash
        WHERE source = 'kofiu'
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
    conn: sqlite3.Connection, *, limit: int = 50, sources=None
) -> list[sqlite3.Row]:
    """extraction_status가 'pending'인 첨부파일 레코드를 반환합니다.

    sources가 주어지면 해당 소스만 선택한다. 비우선 소스의 pending이
    최신순(LIMIT) 윈도우를 점유해 우선 소스가 영구히 굶는 적체를 방지하기
    위함이다(소스 필터를 Python 후속 skip이 아니라 SQL 선택 단계로 끌어올림).
    """
    if sources:
        placeholders = ",".join("?" * len(sources))
        return conn.execute(
            f"""
            SELECT ic.item_id, ic.source_url, ic.label, i.source
            FROM item_content ic
            JOIN items i ON i.id = ic.item_id
            WHERE ic.extraction_status = 'pending'
              AND i.source IN ({placeholders})
            ORDER BY i.published_at DESC
            LIMIT ?
            """,
            (*tuple(sources), limit),
        ).fetchall()
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
