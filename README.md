# Legal·Compliance Signal

금융/노동/개인정보/인권/공정거래 관련 공지(보도자료·입법/행정예고 등)를 매일 자동 수집해 **신규/변경분만** 모아 이메일로 발송하는 스크립트형 앱입니다.

## 지원 소스(8)
- 금융위원회(FSC): 보도자료(RSS), 입법예고/규정변경예고(HTML)
- 금융감독원(FSS): 보도자료(HTML)
- 개인정보보호위원회(PIPC): 보도자료(HTML)
- 고용노동부(MOEL): 보도자료(HTML), 입법·행정예고(HTML)
- 국가인권위원회(NHRCK): 보도자료(HTML)
- 공정거래위원회(KFTC): 보도자료(HTML), 입법·행정예고(HTML), 고시(HTML)
.- 금융정보분석원(KoFIU): 보도자료(HTML), 입법/규정변경 예고(HTML)
.- 대법원(SCOURT): 보도자료/언론보도 해명(HTML, 본문/첨부 포함), 대법원 주요판결(HTML, 요약/원문 링크)
.- 국회 의안정보시스템(NA): 최근 접수/처리 법률안 중 **제목에 ‘보험’이 포함된 법률안** (보험사 관점의 입법 모니터링)

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

## 스케줄링(예시)
### macOS cron(예: 매일 08:30 KST)

```bash
crontab -e
```

아래 한 줄 추가(경로는 환경에 맞게 조정):

```bash
30 8 * * * cd "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/news clipping" && . .venv/bin/activate && python -m briefing run --config config.yaml >> logs/briefing.log 2>&1
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

```bash
# 스냅샷 재생성 (comp_matching_auto 매칭 갱신 시, 분기 1회 권장 — pandas 필요)
python3 scripts/export_company_law_snapshot.py

# 가중치·lexicon 변경 후 전체 재채점 (오프라인, 수 초)
python -m briefing relevance-backfill --config config.yaml
```

