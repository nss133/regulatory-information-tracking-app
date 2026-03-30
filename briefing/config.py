from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


_DEFAULT_KEYCHAIN_SERVICE = "LegalComplianceSignal:GMAIL_APP_PASSWORD"


def _read_password_from_keychain(*, service: str, account: str) -> Optional[str]:
    """
    macOS Keychain에서 generic password를 읽습니다.
    - service: 키체인 서비스명
    - account: 계정(여기서는 Gmail 주소)
    """
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return None
        return (r.stdout or "").strip()
    except Exception:
        return None


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password_env: str

    def password(self) -> str:
        value = os.getenv(self.password_env)
        if not value:
            # 환경변수가 없으면 macOS Keychain에서 1회 저장된 값을 읽어옵니다.
            value = _read_password_from_keychain(
                service=_DEFAULT_KEYCHAIN_SERVICE, account=self.user
            )
        if not value:
            raise RuntimeError(
                f"SMTP 비밀번호가 없습니다. 환경변수 `{self.password_env}`를 설정하거나,\n"
                f"macOS Keychain에 service=`{_DEFAULT_KEYCHAIN_SERVICE}` account=`{self.user}`로 저장하세요."
            )
        # Gmail 앱 비밀번호는 화면에 공백으로 구분되어 표시되는 경우가 있어,
        # 복사/붙여넣기 시 공백이 섞여도 동작하도록 제거합니다.
        return "".join(value.split())


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool
    subject_prefix: str
    from_name: str
    from_email: str
    to: list[str]
    smtp: SmtpConfig


@dataclass(frozen=True)
class FetchConfig:
    user_agent: str
    max_items_per_source: int
    request_timeout_seconds: int


@dataclass(frozen=True)
class StorageConfig:
    sqlite_path: str


@dataclass(frozen=True)
class ComboRules:
    """키워드 조합 규칙. 모든 키워드가 동시에 있을 때 등급을 올리거나 내립니다."""
    promote_to_high: list[tuple[str, ...]]    # 비-HIGH → HIGH 승격
    demote_to_medium: list[tuple[str, ...]]   # HIGH → MEDIUM 강등
    demote_to_low: list[tuple[str, ...]]      # HIGH/MEDIUM → LOW 강등


@dataclass(frozen=True)
class RankingConfig:
    high_keywords: list[str]
    medium_keywords: list[str]
    combo_rules: ComboRules


@dataclass(frozen=True)
class FilterConfig:
    """발송 대상 필터. published_at이 N일 이내인 항목만 포함."""
    max_days_since_published: int


@dataclass(frozen=True)
class LlmConfig:
    enabled: bool
    provider: str
    api_key_env: str
    model: str
    only_when_importance_at_least: str

    def api_key(self) -> Optional[str]:
        return os.getenv(self.api_key_env)


@dataclass(frozen=True)
class AppConfig:
    timezone: str
    storage: StorageConfig
    fetch: FetchConfig
    email: EmailConfig
    ranking: RankingConfig
    filter_config: FilterConfig
    llm: LlmConfig


def _get(d: dict[str, Any], key: str, default: Any = None) -> Any:
    if key not in d:
        if default is not None:
            return default
        raise KeyError(key)
    return d[key]


def _load_combo_rules(raw: dict) -> ComboRules:
    def _parse(key: str) -> list[tuple[str, ...]]:
        return [tuple(str(k) for k in combo) for combo in raw.get(key, [])]

    return ComboRules(
        promote_to_high=_parse("promote_to_high"),
        demote_to_medium=_parse("demote_to_medium"),
        demote_to_low=_parse("demote_to_low"),
    )


def load_config(path: str | Path) -> AppConfig:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("config.yaml 형식이 올바르지 않습니다.")

    storage_raw = _get(raw, "storage")
    fetch_raw = _get(raw, "fetch")
    email_raw = _get(raw, "email")
    smtp_raw = _get(email_raw, "smtp")
    ranking_raw = _get(raw, "ranking")
    filter_raw = raw.get("filter", {})
    llm_raw = _get(raw, "llm")

    return AppConfig(
        timezone=str(_get(raw, "timezone", "Asia/Seoul")),
        storage=StorageConfig(sqlite_path=str(_get(storage_raw, "sqlite_path"))),
        fetch=FetchConfig(
            user_agent=str(_get(fetch_raw, "user_agent")),
            max_items_per_source=int(_get(fetch_raw, "max_items_per_source", 50)),
            request_timeout_seconds=int(_get(fetch_raw, "request_timeout_seconds", 20)),
        ),
        email=EmailConfig(
            enabled=bool(_get(email_raw, "enabled", True)),
            subject_prefix=str(_get(email_raw, "subject_prefix", "[Legal·Compliance Signal]")),
            from_name=str(_get(email_raw, "from_name")),
            from_email=str(_get(email_raw, "from_email")),
            to=list(_get(email_raw, "to")),
            smtp=SmtpConfig(
                host=str(_get(smtp_raw, "host")),
                port=int(_get(smtp_raw, "port")),
                user=str(_get(smtp_raw, "user")),
                password_env=str(_get(smtp_raw, "password_env")),
            ),
        ),
        ranking=RankingConfig(
            high_keywords=list(_get(ranking_raw, "high_keywords", [])),
            medium_keywords=list(_get(ranking_raw, "medium_keywords", [])),
            combo_rules=_load_combo_rules(ranking_raw.get("combo_rules", {})),
        ),
        filter_config=FilterConfig(
            max_days_since_published=int(
                _get(filter_raw, "max_days_since_published", 7)
            ),
        ),
        llm=LlmConfig(
            enabled=bool(_get(llm_raw, "enabled", False)),
            provider=str(_get(llm_raw, "provider", "openai")),
            api_key_env=str(_get(llm_raw, "api_key_env", "OPENAI_API_KEY")),
            model=str(_get(llm_raw, "model", "gpt-4o-mini")),
            only_when_importance_at_least=str(
                _get(llm_raw, "only_when_importance_at_least", "high")
            ),
        ),
    )

