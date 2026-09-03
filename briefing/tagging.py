from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from briefing.utils import now_iso


@dataclass(frozen=True)
class TaggingStats:
    selected: int = 0
    processed: int = 0
    tagged_items: int = 0
    tags: int = 0
    errors: int = 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_verified_manifest(path: str | Path) -> dict:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest.get("sourceFiles", {}).items():
        actual = _sha256_file(manifest_path.parent / name)
        if actual != expected:
            raise RuntimeError(
                f"태거 artifact 해시 불일치: {name} expected={expected} actual={actual}"
            )
    if not manifest.get("profileVersion") or not manifest.get("taxonomySha256"):
        raise RuntimeError("태거 manifest에 profileVersion/taxonomySha256가 없습니다.")
    return manifest


def _stable_tag_id(tag_type: str, canonical: str) -> str:
    value = f"{tag_type}\0{canonical}".encode("utf-8")
    return "tag_" + hashlib.sha256(value).hexdigest()[:24]


def _input_for_row(row, cfg, manifest: dict) -> dict:
    trusted_sources = set(cfg.trusted_body_sources)
    body = row["raw_text"] if row["source"] in trusted_sources else ""
    body = (body or "")[: cfg.max_text_chars]
    summary = (row["summary"] or "")[: cfg.max_text_chars]
    title = row["title"] or ""
    material = json.dumps(
        {
            "profile": manifest["profileVersion"],
            "taxonomy": manifest["taxonomySha256"],
            "title": title,
            "summary": summary,
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "id": row["id"],
        "title": title,
        "summary": summary,
        "body": body,
        "input_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(),
    }


def _node_binary(configured: str) -> str:
    if Path(configured).is_absolute():
        if not Path(configured).is_file():
            raise RuntimeError(f"tagging.node_path가 없습니다: {configured}")
        return configured
    resolved = shutil.which(configured)
    if not resolved:
        raise RuntimeError(f"Node.js 실행 파일을 찾지 못했습니다: {configured}")
    return resolved


def _run_bridge(node: str, script: str, items: list[dict], timeout: int) -> list[dict]:
    payload = {"items": [{k: v for k, v in item.items() if k != "input_hash"} for item in items]}
    result = subprocess.run(
        [node, script],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()[:1200]
        raise RuntimeError(f"뉴스 태거 bridge 실패({result.returncode}): {detail}")
    decoded = json.loads(result.stdout or "{}")
    outputs = decoded.get("results")
    if not isinstance(outputs, list) or len(outputs) != len(items):
        raise RuntimeError("뉴스 태거 bridge 결과 개수가 입력과 일치하지 않습니다.")
    return outputs


def _select_rows(
    conn: sqlite3.Connection,
    *,
    item_ids: Optional[Iterable[str]] = None,
    limit: int = 0,
) -> list[sqlite3.Row]:
    where = ""
    params: list[object] = []
    ids = list(dict.fromkeys(item_ids or []))
    if ids:
        where = f"WHERE i.id IN ({','.join('?' for _ in ids)})"
        params.extend(ids)
    limit_sql = " LIMIT ?" if limit > 0 else ""
    if limit > 0:
        params.append(limit)
    return conn.execute(
        f"""
        SELECT i.id, i.source, i.category, i.title, i.summary, i.raw_text,
               i.published_at, i.relevance_score,
               s.input_hash AS previous_input_hash,
               s.profile_version AS previous_profile_version,
               s.taxonomy_sha256 AS previous_taxonomy_sha256,
               s.status AS previous_status
        FROM items i
        LEFT JOIN item_tag_state s ON s.item_id = i.id
        {where}
        ORDER BY COALESCE(i.published_at, i.last_changed_at) DESC, i.id DESC
        {limit_sql}
        """,
        params,
    ).fetchall()


def _save_result(
    conn: sqlite3.Connection,
    *,
    item: dict,
    result: dict,
    manifest: dict,
    tagged_at: str,
) -> tuple[int, bool]:
    item_id = item["id"]
    tags = result.get("tags") or []
    relations = result.get("relations") or []
    profile = manifest["profileVersion"]
    taxonomy = manifest["taxonomySha256"]
    engine_ids: dict[str, str] = {}

    conn.execute("DELETE FROM item_tag_relations WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
    for tag in tags:
        tag_type = str(tag.get("type") or "issue")
        canonical = str(tag.get("label") or tag.get("canonical") or "").strip()
        if not canonical:
            continue
        tag_id = _stable_tag_id(tag_type, canonical)
        engine_ids[str(tag.get("id") or "")] = tag_id
        aliases_json = json.dumps(tag.get("aliases") or [], ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO tag_registry (
              tag_id, canonical, hashtag, tag_type, tag_type_label, aliases_json,
              producer_package, producer_version, producer_commit,
              profile_version, taxonomy_sha256, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tag_id) DO UPDATE SET
              hashtag=excluded.hashtag,
              tag_type_label=excluded.tag_type_label,
              aliases_json=excluded.aliases_json,
              producer_package=excluded.producer_package,
              producer_version=excluded.producer_version,
              producer_commit=excluded.producer_commit,
              profile_version=excluded.profile_version,
              taxonomy_sha256=excluded.taxonomy_sha256,
              last_seen_at=excluded.last_seen_at
            """,
            (
                tag_id, canonical, tag.get("hashtag"), tag_type, tag.get("typeLabel"),
                aliases_json, manifest.get("producerPackage"), manifest.get("producerVersion"),
                manifest.get("producerCommit"), profile, taxonomy, tagged_at, tagged_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO item_tags (
              item_id, tag_id, score, confidence, candidate_status,
              source_fields_json, evidence_json, aliases_json, occurrences,
              strict_match, profile_version, taxonomy_sha256, input_hash, tagged_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id, tag_id, int(tag.get("score") or 0), tag.get("confidence"),
                tag.get("candidateStatus") or tag.get("status"),
                json.dumps(tag.get("sources") or [], ensure_ascii=False),
                json.dumps(tag.get("evidence") or [], ensure_ascii=False),
                aliases_json, int(tag.get("occurrences") or 0),
                1 if tag.get("strictMatch") else 0, profile, taxonomy,
                item["input_hash"], tagged_at,
            ),
        )

    for relation in relations:
        source_id = engine_ids.get(str(relation.get("sourceId") or ""))
        target_id = engine_ids.get(str(relation.get("targetId") or ""))
        if not source_id or not target_id:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO item_tag_relations (
              item_id, source_tag_id, target_tag_id, relation_type,
              score, evidence_text, status, profile_version, taxonomy_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
            """,
            (
                item_id, source_id, target_id, relation.get("relation") or "related",
                int(relation.get("score") or 0), relation.get("evidence") or "",
                profile, taxonomy, tagged_at,
            ),
        )
    conn.execute(
        """
        INSERT INTO item_tag_state (
          item_id, input_hash, profile_version, taxonomy_sha256,
          status, tag_count, error, tagged_at
        ) VALUES (?, ?, ?, ?, 'success', ?, NULL, ?)
        ON CONFLICT(item_id) DO UPDATE SET
          input_hash=excluded.input_hash,
          profile_version=excluded.profile_version,
          taxonomy_sha256=excluded.taxonomy_sha256,
          status='success', tag_count=excluded.tag_count, error=NULL,
          tagged_at=excluded.tagged_at
        """,
        (item_id, item["input_hash"], profile, taxonomy, len(tags), tagged_at),
    )
    return len(tags), bool(tags)


def _save_errors(
    conn: sqlite3.Connection,
    *,
    items: list[dict],
    manifest: dict,
    tagged_at: str,
    error: Exception,
) -> None:
    detail = f"{type(error).__name__}: {error}"[:1200]
    conn.executemany(
        """
        INSERT INTO item_tag_state (
          item_id, input_hash, profile_version, taxonomy_sha256,
          status, tag_count, error, tagged_at
        ) VALUES (:id, :input_hash, :profile, :taxonomy, 'failed', 0, :error, :tagged_at)
        ON CONFLICT(item_id) DO UPDATE SET
          input_hash=excluded.input_hash,
          profile_version=excluded.profile_version,
          taxonomy_sha256=excluded.taxonomy_sha256,
          status='failed', error=excluded.error, tagged_at=excluded.tagged_at
        """,
        [
            {
                "id": item["id"], "input_hash": item["input_hash"],
                "profile": manifest["profileVersion"],
                "taxonomy": manifest["taxonomySha256"],
                "error": detail, "tagged_at": tagged_at,
            }
            for item in items
        ],
    )


def run_shadow_tagging(
    conn: sqlite3.Connection,
    cfg,
    *,
    item_ids: Optional[Iterable[str]] = None,
    force: bool = False,
    limit: int = 0,
) -> TaggingStats:
    if not cfg.enabled:
        return TaggingStats()
    manifest = load_verified_manifest(cfg.manifest_path)
    node = _node_binary(cfg.node_path)
    rows = _select_rows(conn, item_ids=item_ids, limit=limit)
    prepared = [_input_for_row(row, cfg, manifest) | {"row": row} for row in rows]
    selected = [
        item for item in prepared
        if force
        or item["row"]["previous_status"] != "success"
        or item["row"]["previous_input_hash"] != item["input_hash"]
        or item["row"]["previous_profile_version"] != manifest["profileVersion"]
        or item["row"]["previous_taxonomy_sha256"] != manifest["taxonomySha256"]
    ]
    clean = [{k: v for k, v in item.items() if k != "row"} for item in selected]
    run_id = uuid.uuid4().hex
    started_at = now_iso(cfg.timezone)
    conn.execute(
        """
        INSERT INTO tagging_runs (
          run_id, started_at, status, mode, selected_count,
          profile_version, taxonomy_sha256, producer_commit
        ) VALUES (?, ?, 'running', 'shadow', ?, ?, ?, ?)
        """,
        (run_id, started_at, len(clean), manifest["profileVersion"],
         manifest["taxonomySha256"], manifest.get("producerCommit")),
    )
    conn.commit()

    processed = tagged_items = tag_count = errors = 0
    first_error = None
    for offset in range(0, len(clean), max(1, cfg.batch_size)):
        batch = clean[offset:offset + max(1, cfg.batch_size)]
        tagged_at = now_iso(cfg.timezone)
        try:
            outputs = _run_bridge(node, cfg.bridge_path, batch, cfg.timeout_seconds)
            with conn:
                for item, output in zip(batch, outputs):
                    count, has_tags = _save_result(
                        conn, item=item, result=output, manifest=manifest, tagged_at=tagged_at
                    )
                    processed += 1
                    tag_count += count
                    tagged_items += int(has_tags)
        except Exception as error:  # shadow 실패는 브리핑 판정·발송을 막지 않는다.
            first_error = first_error or error
            errors += len(batch)
            with conn:
                _save_errors(
                    conn, items=batch, manifest=manifest, tagged_at=tagged_at, error=error
                )

    completed_at = now_iso(cfg.timezone)
    status = "completed" if errors == 0 else "partial"
    conn.execute(
        """
        UPDATE tagging_runs
        SET completed_at=?, status=?, processed_count=?, tagged_item_count=?,
            tag_count=?, error_count=?, error=?
        WHERE run_id=?
        """,
        (
            completed_at, status, processed, tagged_items, tag_count, errors,
            str(first_error)[:1200] if first_error else None, run_id,
        ),
    )
    conn.commit()
    return TaggingStats(len(clean), processed, tagged_items, tag_count, errors)


def select_review_queue(conn: sqlite3.Connection, *, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH latest_review AS (
          SELECT item_id, tag_id, decision,
                 ROW_NUMBER() OVER (PARTITION BY item_id, tag_id ORDER BY review_id DESC) AS rn
          FROM tag_reviews
        ), queue AS (
          SELECT i.id AS item_id, i.source, i.category, i.title, i.url,
                 i.published_at, i.relevance_score,
                 tr.tag_id, tr.canonical, tr.hashtag, tr.tag_type,
                 it.score, it.confidence, it.candidate_status,
                 CASE
                   WHEN COALESCE(i.relevance_score, 0) >= 50 AND it.tag_id IS NULL THEN 1
                   WHEN it.candidate_status = '다의어 검토' THEN 2
                   WHEN it.confidence = '검토' THEN 3
                   ELSE 4
                 END AS priority,
                 lr.decision AS latest_decision
          FROM items i
          LEFT JOIN item_tags it ON it.item_id = i.id
          LEFT JOIN tag_registry tr ON tr.tag_id = it.tag_id
          LEFT JOIN latest_review lr
            ON lr.item_id = i.id AND lr.tag_id IS it.tag_id AND lr.rn = 1
          WHERE (
            (COALESCE(i.relevance_score, 0) >= 50
             AND NOT EXISTS (SELECT 1 FROM item_tags x WHERE x.item_id = i.id))
            OR it.candidate_status = '다의어 검토'
            OR (
              it.confidence = '검토'
              AND (
                COALESCE(i.relevance_score, 0) >= 40
                OR (
                  i.importance = 'high'
                  AND i.category IN ('legislation', 'case_law', 'admin_notice')
                )
              )
            )
          )
        )
        SELECT * FROM queue
        WHERE latest_decision IS NULL
        ORDER BY priority, COALESCE(relevance_score, 0) DESC,
                 COALESCE(published_at, '0000-00-00') DESC, score DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def record_tag_review(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    tag_id: Optional[str],
    decision: str,
    replacement_tag_id: Optional[str],
    reason_code: Optional[str],
    note: Optional[str],
    reviewer: str,
    timezone: str,
) -> None:
    allowed = {"accept", "reject", "edit", "missed"}
    if decision not in allowed:
        raise ValueError(f"decision은 {sorted(allowed)} 중 하나여야 합니다.")
    if decision != "missed" and not tag_id:
        raise ValueError("accept/reject/edit 판정에는 tag_id가 필요합니다.")
    if decision == "edit" and not replacement_tag_id:
        raise ValueError("edit 판정에는 replacement_tag_id가 필요합니다.")
    conn.execute(
        """
        INSERT INTO tag_reviews (
          item_id, tag_id, decision, replacement_tag_id,
          reason_code, note, reviewer, reviewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, tag_id, decision, replacement_tag_id, reason_code, note,
         reviewer, now_iso(timezone)),
    )
    conn.commit()


def search_items_by_tags(
    conn: sqlite3.Connection,
    *,
    tags: Iterable[str],
    tag_types: Optional[Iterable[str]] = None,
    match_all: bool = False,
    limit: int = 20,
) -> list[sqlite3.Row]:
    values = [str(value).lstrip("#").strip() for value in tags if str(value).strip()]
    if not values:
        return []
    type_values = [str(value).strip() for value in (tag_types or []) if str(value).strip()]
    tag_placeholders = ",".join("?" for _ in values)
    type_sql = ""
    params: list[object] = values[:]
    if type_values:
        type_sql = f" AND tr.tag_type IN ({','.join('?' for _ in type_values)})"
        params.extend(type_values)
    having = "HAVING COUNT(DISTINCT tr.canonical) = ?" if match_all else ""
    if match_all:
        params.append(len(set(values)))
    params.append(limit)
    return conn.execute(
        f"""
        SELECT i.id, i.source, i.category, i.title, i.url, i.published_at,
               i.importance, i.relevance_score,
               GROUP_CONCAT(DISTINCT tr.hashtag) AS matched_tags,
               MAX(it.score) AS max_tag_score
        FROM items i
        JOIN item_tags it ON it.item_id = i.id
        JOIN tag_registry tr ON tr.tag_id = it.tag_id
        WHERE (tr.canonical IN ({tag_placeholders})
               OR LTRIM(tr.hashtag, '#') IN ({tag_placeholders}))
          {type_sql}
        GROUP BY i.id
        {having}
        ORDER BY COALESCE(i.published_at, '0000-00-00') DESC, max_tag_score DESC
        LIMIT ?
        """,
        values + params,
    ).fetchall()
