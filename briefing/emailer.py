from __future__ import annotations

import smtplib
from email.headerregistry import Address
from email.message import EmailMessage

from briefing.config import EmailConfig


def send_email(*, cfg: EmailConfig, subject: str, html_body: str, text_body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = Address(display_name=cfg.from_name, addr_spec=cfg.from_email)
    msg["To"] = ", ".join(cfg.to)

    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    password = cfg.smtp.password()
    with smtplib.SMTP(cfg.smtp.host, cfg.smtp.port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        try:
            s.login(cfg.smtp.user, password)
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(
                "Gmail SMTP 인증 실패(535)입니다. 아래를 확인하세요:\n"
                "- config.yaml의 email.smtp.user가 실제 Gmail 주소인지\n"
                "- config.yaml의 email.from_email이 같은 Gmail 주소인지(권장)\n"
                "- 2단계 인증이 켜져 있고 '앱 비밀번호'를 발급받았는지\n"
                "- GMAIL_APP_PASSWORD 환경변수 값에 공백/줄바꿈이 섞이지 않았는지\n"
                "(참고: 본 프로젝트는 공백은 자동 제거합니다)\n"
                f"원본 에러: {e}"
            ) from e
        s.send_message(msg)

