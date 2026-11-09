"""수동 브리핑 요청 시 사용하는 리포트 후보 조회·작성·PDF 생성 명령."""
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from briefing.config import load_config
from briefing.reports import build_pdf, report_candidates


def _rows(args):
    cfg=load_config(args.config)
    database=Path(cfg.storage.sqlite_path).resolve()
    # 운영 DB에 쓰기나 WAL 설정을 하지 않는다.
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        if getattr(args,'item_id',None):
            rows=conn.execute('SELECT * FROM items WHERE id=?',(args.item_id,)).fetchall()
        else:
            rows=conn.execute("SELECT * FROM items WHERE first_seen_at >= datetime('now', ?) OR last_changed_at >= datetime('now', ?) ORDER BY last_changed_at DESC",(f'-{args.days} day',f'-{args.days} day')).fetchall()
        return [SimpleNamespace(**dict(r)) for r in rows]


def cmd_candidates(args):
    candidates=report_candidates(_rows(args))
    text=json.dumps(candidates,ensure_ascii=False,indent=2)+'\n'
    if args.out:
        p=Path(args.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        print(f'상세 리포트 검토 후보 {len(candidates)}건: {p}')
    else: print(text)
    return 0


def cmd_init(args):
    items=_rows(args)
    if not items:raise ValueError('수집 항목을 찾을 수 없음')
    item=items[0]
    report={'report_id':args.report_id,'revision':1,'status':'draft',
        'kind':'legislation' if item.source=='na' else 'regulation',
        'title':item.title,'as_of':date.today().isoformat(),
        'stage':'발의·제출' if item.source=='na' else '의결 발표',
        'selection':{'relevance':'','explanation_value':'','evidence':''},
        'overview':[], 'key_points':[], 'industry_impact':'', 'business_reference':[],
        'verification_scope':'',
        'sources':[{'item_id':item.id,'content_hash':item.content_hash,'label':item.title,'url':item.url}]}
    p=Path(args.out);p.parent.mkdir(parents=True,exist_ok=True)
    # 기존 작성본 보존. 초안의 단계·유형 역시 원문 확인 후 수정한다.
    with p.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(f'작성용 초안: {p} (단계·내용 확인 필요, 자동 첨부 제외)')
    return 0


def cmd_build(args):
    report=json.loads(Path(args.input).read_text())
    path=build_pdf(report,args.out,font_path=args.font,bold_font_path=args.bold_font)
    print(f'PDF 생성: {path} ({report["status"]})')
    return 0


def register_commands(sub):
    p=sub.add_parser('report-candidates',help='공용 상세 리포트 검토 후보 조회(읽기 전용)')
    p.add_argument('--config',required=True);p.add_argument('--days',type=int,default=7);p.add_argument('--out')
    p.set_defaults(_fn=cmd_candidates)
    p=sub.add_parser('report-init',help='수집 원문에 연결된 작성용 JSON 초안 생성')
    p.add_argument('--config',required=True);p.add_argument('--item-id',required=True)
    p.add_argument('--report-id',required=True);p.add_argument('--out',required=True)
    p.set_defaults(_fn=cmd_init)
    p=sub.add_parser('report-build',help='작성·검수한 JSON으로 오프라인 PDF 생성')
    p.add_argument('--input',required=True);p.add_argument('--out',required=True);p.add_argument('--font');p.add_argument('--bold-font')
    p.set_defaults(_fn=cmd_build)
