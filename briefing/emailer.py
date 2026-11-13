from __future__ import annotations

import smtplib
from email.headerregistry import Address
from email.message import EmailMessage

from briefing.config import EmailConfig
from briefing.reports import PdfAttachment
from briefing.review_gate import validate_review


def send_error_alert(*, cfg: EmailConfig, errors: list[str], run_date: str) -> None:
    """수집 오류 발생 시 관리자(발신 주소)에게만 경보 이메일 발송."""
    if not errors or not cfg.enabled:
        return
    subject = f"[Legal·Compliance Signal] 수집 오류 {len(errors)}건 ({run_date})"
    lines = "\n".join(f"- {e}" for e in errors)
    text_body = f"수집 오류 목록:\n\n{lines}"
    html_body = f"<pre style='font-family:monospace'>{text_body}</pre>"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = Address(display_name=cfg.from_name, addr_spec=cfg.from_email)
    msg["To"] = cfg.from_email  # 관리자(발신자)에게만
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        password = cfg.smtp.password()
        with smtplib.SMTP(cfg.smtp.host, cfg.smtp.port, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(cfg.smtp.user, password)
            s.send_message(msg)
    except Exception as e:
        print(f"오류 알림 이메일 발송 실패: {e}")


def send_email(
    *, cfg: EmailConfig, subject: str, html_body: str, text_body: str,
    message_id: str | None = None,
    attachments: tuple[PdfAttachment, ...] = (),
    review_context: dict | None = None,
) -> str:
    if not review_context or review_context['rendered'].html != html_body:
        raise ValueError('검수된 본문과 발송 문맥이 없어 SMTP 발송을 차단함')
    validate_review(**dict(review_context, recipients=cfg.to, attachments=attachments))
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = Address(display_name=cfg.from_name, addr_spec=cfg.from_email)
    msg["To"] = ", ".join(cfg.to)
    if message_id:
        msg["Message-ID"] = message_id

    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    # CID images belong to the HTML alternative, so clients can display them offline.
    html_part = msg.get_payload()[-1]
    for attachment in attachments:
        if attachment.preview_png:
            html_part.add_related(attachment.preview_png, maintype="image", subtype="png",
                                  cid=f"<{attachment.preview_cid}>", disposition="inline",
                                  filename=attachment.report_id + "-preview.png")

    for attachment in attachments:
        if not attachment.content.startswith(b"%PDF-"):
            raise ValueError(f"올바른 PDF가 아님: {attachment.filename}")
        msg.add_attachment(attachment.content, maintype="application", subtype="pdf",
                           filename=attachment.filename)

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
    return str(msg["Message-ID"] or "")


def send_admin_alert(
    *, cfg: EmailConfig, subject: str, text_body: str,
    message_id: str | None = None,
) -> str:
    """운영 감시 경보를 관리자(발신 주소)에게 보내며 실패는 호출자에게 전달한다."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = Address(display_name=cfg.from_name, addr_spec=cfg.from_email)
    msg["To"] = cfg.from_email
    if message_id:
        msg["Message-ID"] = message_id
    msg.set_content(text_body)

    password = cfg.smtp.password()
    with smtplib.SMTP(cfg.smtp.host, cfg.smtp.port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(cfg.smtp.user, password)
        s.send_message(msg)
    return str(msg["Message-ID"] or "")
