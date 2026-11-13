# 브리핑 사안 편집·기사 큐레이션

2026.11.11. 승인 사항. 기존 글자 크기와 폭, LOW 표현, 국회 단계 필터를 유지한다. 제목 링크가 원문이며, 주요 항목 오른쪽에는 첨부·같은 사안의 다른 공식 자료·관련 보도만 둔다. PDF 리포트는 메일 첨부로 제공하는 기존 방식이다.

## 발송 준비

1. 기존 방식으로 당일 공식 자료를 수집·요약한다. 기사 검색·본문 확인은 발송 준비 중에 수행하며, 검색 결과 제목만 보고 연결하지 않는다.
2. 필요 사안만 아래 명령으로 초안을 만든다. 같은 사안의 공식 원문은 `--item-id`를 반복해 연결한다. 파일이 이미 있으면 덮어쓰지 않는다.

```sh
.venv/bin/python -m briefing editorial-init --config config.yaml --date 2026-11-11 \
  --item-id kftc:press:48199 --item-id kftc:lawnote:48209 \
  --primary-item-id kftc:lawnote:48209 --out editorial/2026-11-11.json
```

3. 파일의 `issues`에 사안을 추가하고 공식 자료에 맞게 `summary`·`stage`를 작성한다. 예고는 확정 규범과 구분한다. 요약 보강이 필요 없으면 `summary: null`로 기존 요약을 유지한다. `articles`는 사안당 최대 2건이며, 비어 있어도 된다.
4. 원문과 기사 확인 후 파일 전체를 `status: ready`로 바꾸고 검증한다. 각 연결 원문의 ID·URL·현재 버전·본문 검증값을 임의로 최신 값으로 바꾸어 검증을 우회하지 않는다. 자료가 달라지면 내용을 다시 대조한다.

```sh
.venv/bin/python -m briefing editorial-check --config config.yaml --input editorial/2026-11-11.json
.venv/bin/python -m briefing editorial-preview --config config.yaml \
  --input editorial/2026-11-11.json --out output/editorial/today.html
```

5. 이후 `run --manual-request`·`preview`·`resend-last --manual-request`가 당일 `editorial/YYYY-MM-DD.json`을 읽는다. 날짜가 다른 편집본이나 초안을 임의로 이어 쓰지 않는다. ready 파일이 없으면 미리보기만 가능하고 발송은 차단한다. 미리보기에서 기사 링크를 생략한 것을 검수 완료로 간주하지 않는다. 새 API·자동발송 일정·외부 게시 서비스는 사용하지 않는다.

이미 발송한 배치를 검토할 때는 발송 상태를 초기화하지 않고 보관본으로 재현한다.

```sh
.venv/bin/python -m briefing editorial-preview --config config.yaml \
  --archive logs/deliveries/YYYY-MM-DD/HHMMSS-RUN.json \
  --input editorial/YYYY-MM-DD.json --out output/editorial/review.html
```

세 `editorial-*` 명령은 DB 읽기 전용이며 수집·SMTP·발송 상태를 변경하지 않는다. 보관본 이후 원문 버전이 바뀌면 재현을 중단한다. 당일 편집 파일은 Git에서 제외하고, 작성 예제는 `editorial/examples/draft-issue.json`에 둔다.

## 기사·일정 입력

기사의 필수 항목은 `title`, `url`(HTTPS), `publisher`, `published_at`, `checked_at`, `why_read`이다. 발행일 ≤ 확인일 ≤ 기준일이어야 하며 제목 원문 URL과 중복하지 않는다. 배경·사례·업계 반응·쟁점 등 읽을 이유가 있는 보도를 선별하고, 공식 발표를 그대로 반복하는 기사만 있으면 비워둔다. 언론의 평가를 공식 판단으로 바꾸거나, 초기 입법자료를 관련 기사를 통해 우회 노출하지 않는다.

사람이 확인한 일정은 `deadlines`에 `label`(의견제출/시행일/신청기한), `date`(ISO), `source_item_id`, `source_text`(원문의 실제 구절)를 넣는다. 근거 구절과 날짜가 맞지 않으면 발송 전에 중단한다. 원문이 월·일만 표시한 경우 `year_from_publication: true`를 명시하면 해당 공식 자료의 게시연도와 대조한다. 연도 넘어가는 일정을 이 방식으로 추정하지 않는다.

## 묶음과 기록

- 자동 묶음은 같은 기관의 보도자료·예고문, 같은 개정 대상과 예고 종류, 게시일 차이 3일 이내인 일대일 대응만 허용한다. 날짜·대상·단계가 불명확하거나 후보가 여럿이면 자동 연결하지 않는다. 검토한 편집본은 더 명확한 연결을 지정할 수 있다.
- 묶음은 하나의 요약과 연결 원문 목록으로 표시한다. 중요도는 구성원의 가장 높은 표시 등급을 유지하고 모든 원문 ID를 `visible_item_ids`에 남긴다. 섹션에는 필요한 경우 사안 수와 원문 수를 구분한다.
- 개별 첨부 링크는 상세페이지 본문에서 이름이 확인된 자료만 사용하며, 오래된 파일명·다른 호스트의 링크를 복원하지 않는다. 같은 문서의 PDF/HWP 쌍은 PDF를 우선하지만 별첨·대비표·전문은 보존한다.
- 발송 HTML과 텍스트 양쪽에 같은 편집 내용·참고기사를 반영한다. 발송 JSON의 `editorial`에 연결 원문, 검토 요약·상태·일정, 기사 URL·언론사·날짜·읽을 이유를 보관한다. 기존 SMTP 모호 상태 중복 방지는 유지한다.

요약 생성은 자료 유형별 핵심 내용과 조건·예외·잠정 수치를 보존하도록 안내한다. 긴 본문은 처음 부분과 끝부분, 시행·부칙·예외 주변 문맥을 함께 선택한다. 모델 출력이 항상 정확하다는 보장은 아니므로 중요한 항목은 발송 전 공식 자료와 대조한다. 기존 요약을 일괄 덮어쓰거나 과거 발송을 초기화하지 않는다.


## 최종 검수 필수 — 2026.11.13.

기존 오전 8시 자동발송과 오전 9시 자동 경보 메일을 비활성화했다. 사용자의 일일 발송 요청을 받은 뒤 자료 수집·편집·미리보기 확인까지 완료하고 다음 명령을 실행한다. 이 검수는 운영자가 수행하며 사용자에게 매번 별도 승인 절차를 요구하는 것이 아니다.

```sh
.venv/bin/python -m briefing delivery-review --config config.yaml \
  --note '본문·원문·첨부·선별 기사·PDF 및 배포처를 대조함'
.venv/bin/python -m briefing run --manual-request --config config.yaml
# 또는 ./scripts/run_daily.sh --manual-request config.yaml
```

`logs/reviews/YYYY-MM-DD.html`과 JSON 검수 기록이 생성된다. 대상 원문·본문·편집본·최종 HTML·수신자·PDF 작성 근거를 묶어 검증한다. 발송 때 다시 대조하며 변경되면 재검수가 필요하다. PDF 재생성 시 메타데이터 때문에 달라지는 CID만 정규화하고 PDF 작성 JSON은 별도로 확인한다. 발송 원장 JSON에는 검수 기록도 보존한다.

오른쪽 자료가 0개라면 기본적으로 검수를 완료할 수 없다. 실제로 공식 첨부와 관련 보도를 확인해 추가자료가 없을 때만 `--no-references-reason '확인한 대상과 생략 사유'`를 명시한다. 기사 링크를 억지로 채우거나 내용 없는 오른쪽 상자를 만드는 방식으로 통과시키지 않는다.

`delivery-review --resend-last`는 직전 배치의 재발송 검토용이며 메일을 보내지 않는다. 실제 재발송에도 명시적 사용자 요청, `resend-last --manual-request`, 일치하는 당일 검수 기록이 필요하다. 과거 검수 기록을 복사해서 사용하지 않는다. 수집 결과가 갱신되면 발송이 차단될 수 있으므로 바뀐 자료를 확인한 후 다시 검수한다.
