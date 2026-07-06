#!/usr/bin/env bash
set -euo pipefail

# launchd(WorkingDirectory) 또는 스크립트 경로 기준
ROOT_DIR="$(pwd)"
[[ -d "$ROOT_DIR/.venv" ]] || ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  echo "ERROR: .venv가 없습니다. README의 설치 절차를 먼저 진행하세요." >&2
  exit 1
fi

source .venv/bin/activate

CONFIG="${1:-config.yaml}"

# config에서 smtp.user(account) + llm.api_key_env(키 이름) 읽기
read -r ACCOUNT LLM_KEY_ENV <<<"$(python3 -c "
import yaml
try:
    c = yaml.safe_load(open('$CONFIG'))
    print(
        (c.get('email', {}).get('smtp', {}).get('user', '') or ''),
        (c.get('llm', {}).get('api_key_env', '') or ''),
    )
except Exception:
    print('', '')
" 2>/dev/null)"

# Keychain 자동 로드: 서비스명 = DailyRegulatoryBriefing:<KEY_NAME>, 계정 = smtp.user
load_from_keychain() {
  local key_name="$1"
  if [[ -z "$ACCOUNT" || -z "$key_name" ]]; then return 0; fi
  if [[ -n "${!key_name:-}" ]]; then return 0; fi  # 이미 환경변수로 설정됨
  local val
  val=$(security find-generic-password \
    -s "DailyRegulatoryBriefing:${key_name}" \
    -a "$ACCOUNT" -w 2>/dev/null || true)
  if [[ -n "$val" ]]; then
    export "${key_name}=${val}"
  fi
}

# SMTP 비밀번호
load_from_keychain "GMAIL_APP_PASSWORD"
# LLM API 키 (config의 llm.api_key_env 기준 — DEEPSEEK_API_KEY, GROQ_API_KEY 등)
load_from_keychain "$LLM_KEY_ENV"

python -m briefing run --config "$CONFIG"

# 법원 판결 인덱스 자동 갱신 (수집 직후 — 신규 판결을 vault 판례(근거) 인덱스에 반영).
# 실패해도 수집 결과는 유지하도록 non-fatal 처리.
if [[ -f "build_court_index.py" ]]; then
  python build_court_index.py || echo "WARN: 법원 판결 인덱스 갱신 실패(무시하고 계속)" >&2
fi

