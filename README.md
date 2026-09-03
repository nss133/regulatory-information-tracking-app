# Legal·Compliance Signal

금융/노동/개인정보/인권/공정거래 관련 공지(보도자료·입법/행정예고 등)를 매일 자동 수집해 **신규/변경분만** 모아 이메일로 발송하는 스크립트형 앱입니다.

## 지원 소스(9)
- 금융위원회(FSC): 보도자료(RSS), 입법예고/규정변경예고(HTML)
- 금융감독원(FSS): 보도자료(HTML)
- 개인정보보호위원회(PIPC): 보도자료(HTML)
- 고용노동부(MOEL): 보도자료(HTML), 입법·행정예고(HTML)
- 국가인권위원회(NHRCK): 보도자료(HTML)
- 공정거래위원회(KFTC): 보도자료(HTML), 입법·행정예고(HTML), 고시(HTML)
- 금융정보분석원(KoFIU): 보도자료·입법/규정변경예고(JSON API)
- 대법원(SCOURT): 보도자료/언론보도 해명과 중요판결·결정 요지(HTML, 본문/첨부 포함). 중복되는 법원도서관 개별 주요판결은 수집하지 않습니다.
- 국회 의안정보시스템(NA): 최근 접수/처리 법률안 중 **제목에 ‘보험’이 포함된 법률안** (보험사 관점의 입법 모니터링)

## 빠른 시작
### 1) 가상환경 생성 및 의존성 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) 설정 파일 준비
`config.example.yaml`을 복사해 `config.yaml`을 만드세요.

```bash
cp config.example.yaml config.yaml
```

Gmail SMTP는 **앱 비밀번호** 사용을 권장합니다.

#### (권장) macOS Keychain에 1회 저장해서 매번 입력 없애기
1회만 저장하면 이후부터는 `export` 없이도 자동으로 발송됩니다.

```bash
chmod +x scripts/setup_keychain_gmail_app_password.sh
./scripts/setup_keychain_gmail_app_password.sh curraheec@gmail.com
```

키체인 저장 키는 아래와 같습니다.
- **service**: `DailyRegulatoryBriefing:GMAIL_APP_PASSWORD`
- **account**: `config.yaml`의 `email.smtp.user` (예: `curraheec@gmail.com`)

#### (대안) 세션마다 환경변수 export
```bash
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
```

### 3) 실행(미리보기 / 실제 발송)

```bash
python -m briefing preview --config config.yaml --out daily.html
python -m briefing run --config config.yaml
```

`cron/launchd`에서 `scripts/run_daily.sh`를 사용하려면:

```bash
chmod +x scripts/run_daily.sh
```

## 실행 결과(개념)
- `data/briefing.sqlite3`에 수집/발송 상태가 저장됩니다.
- 이미 발송된 항목은 재발송하지 않습니다.
- 제목/첨부링크 등이 바뀐 경우 **변경**으로 다시 잡아 재발송합니다.
- 상태 태그는 처음 발견한 항목에만 `신규`로 표시하며, 갱신 재발송 항목에는 별도 상태 태그를 붙이지 않습니다.
- 입법·행정예고 원문에 명시된 의견제출·신청·시행 날짜를 추출해 해당 항목에 표시합니다.
- 지난 일정은 메일에서 숨기며, 판결문에 인용된 과거 법령 연혁은 일정으로 추출하지 않습니다.
- 메일 편집 정책으로 가려진 항목은 발송 완료와 구분해 저장하며, 내용이 바뀌면 다시 평가합니다.
- 제목의 `브리핑 N건 · 수집 M건`은 실제 노출 건수와 수집 후보 건수를 구분합니다.
- ★ 항목은 당사 관련성 근거(매칭 법령·키워드)를 메일 본문에 함께 표시합니다.
- 대법원 중요판결·결정 요지는 본문을 3~4줄로 요약하며, 같은 내용을 다시 싣는 법원도서관 개별 주요판결은 메일에서 제외합니다.
- 소스별 최종 성공 시각·수집 건수·연속 실패를 기록하고, 점검 명령에서 누락·지연을 탐지합니다.
- 실행·발송 단계와 건수, SMTP Message-ID를 `delivery_runs`에 기록합니다. SMTP 접수 결과가
  불명확한 동일 배치는 자동 재발송을 차단하며, 발송 HTML과 JSON 원장은
  `logs/deliveries/YYYY-MM-DD/`에 보관합니다.

최근 발송 배치 확인:

```bash
python -m briefing delivery-status --config config.yaml --limit 10
```

운영 대시보드·백업 무결성 점검·로그 순환:

```bash
python -m briefing operations --config config.yaml --rotate-logs
open logs/status.html
```

- `logs/status.html`: 최근 실행과 소스별 성공·오류 현황
- `data_backup/briefing_weekly.sqlite3`: 매주 월요일 온라인 백업 후 `quick_check`
- `logs/*.log`: 파일당 5MB 도달 시 최근 5개까지 순환 보관

## 스케줄링(예시)
### macOS launchd(권장)

현재 구성과 배포 대상을 먼저 확인한 뒤 `--apply`로 실제 반영합니다. 설치기는 로컬
자동실행본을 갱신하고 기존 로컬 DB를 보존한 다음, 중복 cron을 제거해 평일 08:00
브리핑과 평일 09:00 미실행 감시 LaunchAgent를 등록합니다. 감시는 당일 실행이 없거나
실패·중단·확인 필요 상태일 때 관리자에게 날짜별 한 번만 경고합니다. 설치 직후에는
메일을 보내지 않습니다.

```bash
./scripts/install_launchd.sh
./scripts/install_launchd.sh --apply
```

### macOS cron(예: 매일 08:30 KST)

```bash
crontab -e
```

아래 한 줄 추가(경로는 환경에 맞게 조정):

```bash
30 8 * * * cd "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/news clipping" && .venv/bin/python -m briefing run --config config.yaml >> logs/briefing.log 2>&1
```

## Docker(옵션)
`Dockerfile` / `docker-compose.yml`을 사용해 컨테이너로 실행할 수 있습니다.

```bash
export GMAIL_APP_PASSWORD="..."
docker compose build
docker compose run --rm briefing
```

스케줄링은 호스트의 cron/launchd(또는 클라우드 스케줄러)에서 컨테이너 실행을 트리거하는 방식이 가장 단순합니다.

## LLM 요약/중요도(옵션)
- 기본은 **키워드 기반 중요도**만 사용합니다.
- 외부 LLM API 전송 정책이 확정되면 `config.yaml`에서 `llm.enabled: true`로 켜고, API 키 환경변수를 설정해 활성화합니다.

## 당사 관련성(relevance) 채점 — ★ 배지
중요도(high/medium/low)와 별개로, **미래에셋생명 직결도**를 0~100점으로 채점합니다.

- **L1**: comp_matching_auto의 사규-법령 매칭 산출물(감시법령 155개 + 생보협회 규정 50개)을
  스냅샷(`data/company_relevance_snapshot.json`)으로 받아 제목·요약에서 법령명을 감지.
  법령별 사규 연계 수가 보조 가중치로 반영됩니다.
- **L2**: `config.yaml`의 `relevance.term_weights` — 생보 특화 키워드(변액·퇴직연금·K-ICS 등).
- 입법예고·행정예고는 규범 변경 직접 신호라 관련 흔적이 있으면 +15 가산.
- 채점 입력은 제목+LLM요약(+대법원·국회 본문)만 사용 — HTML 스크랩 본문은 사이트 메뉴
  보일러플레이트 오탐 때문에 제외(`briefing/relevance.py`의 `build_score_text` 참조).

**노출**: 등급 체계는 그대로 두고, 각 등급 안에서 relevance 점수순 정렬 +
50점 이상 항목에 ★ 배지와 근거 주석(매칭 법령·키워드)을 표시합니다.
임계값: `briefing/render.py`의 `RELEVANCE_STAR_THRESHOLD`.

생명보험 직접 신호와 손해보험 전용 신호를 별도 판정합니다. 자동차보험·대차료 등
손보 전용 신호만 있는 항목은 일반 보험어가 함께 있어도 29점으로 제한되어 ★가
붙지 않으며, 대법원 주요판결에서는 LOW(손해보험 전용)로 분류됩니다.

```bash
# 스냅샷 재생성 (comp_matching_auto 매칭 갱신 시, 분기 1회 권장 — pandas 필요)
python3 scripts/export_company_law_snapshot.py

# 가중치·lexicon 변경 후 전체 재채점 (오프라인, 수 초)
python -m briefing relevance-backfill --config config.yaml
```

## 법률 태그 shadow 인덱스

`legal-opinion-tagger`의 커밋된 엔진·쟁점카드 스냅샷을 이용해 법령·조문·쟁점 태그를
별도 정규화 테이블에 저장합니다. 이 태그는 검색과 사람 검수에만 쓰이며 기존 중요도,
★ 관련성, 메일 노출·발송 판단을 바꾸지 않습니다. 엔진 파일은
`vendor/legal-opinion-tagger/manifest.json`의 SHA-256과 일치할 때만 실행됩니다.

```bash
# 전체 DB shadow 태깅(입력·profile 해시가 같은 항목은 자동 건너뜀)
python -m briefing tag-backfill --config config.yaml

# 다의어·저신뢰 태그와 미태깅 ★ 항목 확인
python -m briefing tag-review-queue --config config.yaml --limit 50

# 태그 기반 검색(여러 --tag를 쓰고 --match-all로 AND 검색 가능)
python -m briefing tag-search --config config.yaml --tag 보험업법 --tag 설명의무 --match-all

# 사람 검수 이력 기록
python -m briefing tag-review --config config.yaml \
  --item-id ITEM_ID --tag-id TAG_ID --decision accept --reason-code direct_evidence
```

MCP `news_search`도 `tags`, `tag_types`, `tag_mode(any|all)` 필터를 지원합니다. 검색어 없이
태그만 지정할 수도 있고, 검색어와 태그를 함께 주면 FTS 결과와 AND 결합합니다. 태그는
후보 탐색용이며 실제 인용은 계속 원문·첨부 발췌와 URL로 확인해야 합니다.

## J-ID 영향후보 검수 큐

규제동향에서 기존 판단의 근거와 겹치는 대상을 별도 큐에 저장합니다. 같은 법령명이
나왔다는 이유만으로 개별 J-ID에 영향이 있다고 보지 않고, `정확 조문`·`정확 판례`를
우선 표시합니다. 상태는 `unreviewed`, `no_effect`, `affected`, `deferred`이며 모든
사람 판정은 이력으로 남습니다. `affected`로 판정해도 J-ID 원문은 자동 수정하지 않습니다.

```bash
# 최근 60일 데이터로 최초 큐 구성
python -m briefing jid-impact-backfill --config config.yaml --days 60

# 미검수 후보(정확 일치 우선) / 정확 일치만 보기
python -m briefing jid-impact-queue --config config.yaml
python -m briefing jid-impact-queue --config config.yaml --exact-only

# 검수 결과와 사유 기록
python -m briefing jid-impact-review --config config.yaml \
  --candidate-id impact_... --decision affected --reason "개정 조문이 기존 결론의 전제 변경"
```
