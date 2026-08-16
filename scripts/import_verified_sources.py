#!/usr/bin/env python3
"""미리 확보한 공식 응답으로 선택 소스를 비발송 보정한다.

네트워크가 차단된 진단 환경에서만 쓰는 운영 보조 도구다. 메일은 보내지 않으며,
명시한 공식 응답 파일만 파싱해 DB에 upsert한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from briefing.config import load_config
from briefing.db import (
    connect,
    init_db,
    mark_old_kofiu_announce_as_sent,
    record_source_fetches,
    upsert_items,
)
from briefing.sources.fsc import FscConnector
from briefing.sources.kofiu import KofiuConnector
from briefing.sources.nhrck import NhrckConnector
from briefing.sources.urls import (
    FSC_LEGISLATION_LIST,
    FSC_PRESS_RSS,
    KOFIU_BOARD_LIST_API,
    KOFIU_LAW_LIST_API,
    NHRCK_PRESS_LIST,
)


class FixtureHttp:
    def __init__(self, mapping):
        self.mapping = mapping

    def get_text(self, url, *, params=None, extra_headers=None):
        key = (url, (params or {}).get("seCd"))
        if key in self.mapping:
            return self.mapping[key]
        if url in self.mapping:
            return self.mapping[url]
        raise KeyError(f"공식 응답 파일 미지정: {key}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--fsc-rss", required=True)
    p.add_argument("--fsc-legislation", required=True)
    p.add_argument("--nhrck", required=True)
    p.add_argument("--kofiu-press", required=True)
    p.add_argument("--kofiu-announce", required=True)
    p.add_argument("--kofiu-legislation", required=True)
    p.add_argument(
        "--clear-diagnostic-failures",
        action="store_true",
        help="진단 환경 네트워크 실패를 기존 DB 최종 관측 성공으로 복원",
    )
    args = p.parse_args()

    read = lambda value: Path(value).read_text(encoding="utf-8")
    mapping = {
        FSC_PRESS_RSS: read(args.fsc_rss),
        FSC_LEGISLATION_LIST: read(args.fsc_legislation),
        NHRCK_PRESS_LIST: read(args.nhrck),
        (KOFIU_BOARD_LIST_API, "0001"): read(args.kofiu_press),
        (KOFIU_LAW_LIST_API, "001"): read(args.kofiu_announce),
        (KOFIU_LAW_LIST_API, "002"): read(args.kofiu_legislation),
    }
    http = FixtureHttp(mapping)
    cfg = load_config(args.config)
    connectors = [
        FscConnector(http, max_items=cfg.fetch.max_items_per_source),
        NhrckConnector(http, max_items=cfg.fetch.max_items_per_source),
        KofiuConnector(http, max_items=cfg.fetch.max_items_per_source),
    ]
    conn = connect(cfg.storage.sqlite_path)
    init_db(conn)
    all_items = []
    outcomes = {}
    for connector in connectors:
        items = connector.fetch_latest()
        all_items.extend(items)
        outcomes[connector.code] = (len(items), None, 0.0)
        print(f"{connector.code}: {len(items)}건")
    upsert_items(conn, all_items, tz_name=cfg.timezone)
    record_source_fetches(conn, outcomes, tz_name=cfg.timezone)
    baseline = mark_old_kofiu_announce_as_sent(conn, tz_name=cfg.timezone)
    if args.clear_diagnostic_failures:
        conn.execute(
            """
            UPDATE source_health
            SET last_attempt_at = COALESCE(
                  (SELECT MAX(i.last_seen_at) FROM items i
                   WHERE i.source = source_health.source),
                  last_attempt_at
                ),
                last_success_at = COALESCE(
                  last_success_at,
                  (SELECT MAX(i.last_seen_at) FROM items i
                   WHERE i.source = source_health.source)
                ),
                last_item_count = COALESCE(
                  (SELECT COUNT(*) FROM items i
                   WHERE i.source = source_health.source
                     AND i.last_seen_at = (
                       SELECT MAX(i2.last_seen_at) FROM items i2
                       WHERE i2.source = source_health.source
                     )),
                  last_item_count
                ),
                last_error = NULL,
                consecutive_failures = 0
            WHERE source NOT IN ('fsc', 'nhrck', 'kofiu')
            """
        )
        conn.commit()
    print(f"DB 반영 {len(all_items)}건, KoFIU 과거 기준선 {baseline}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
