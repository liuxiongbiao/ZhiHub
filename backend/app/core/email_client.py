"""
backend/app/core/email_client.py

职责: 邮箱激活邮件发送（PRD §3.1.1 邮箱注册验证链接）

设计:
  - SMTP 配置为空时: development/test 由调用方决定降级（日志/返回链接），生产已在
    config 启动校验拒绝（SMTP_HOST 缺失即启动失败）。
  - 使用标准库 smtplib + email.message，无额外依赖。
"""

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    """邮件发送失败（SMTP 未配置或网络/认证错误）。"""


def is_smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_email(*, to: str, subject: str, text: str) -> None:
    """发送纯文本邮件。SMTP 未配置或发送失败抛 EmailSendError。"""
    if not is_smtp_configured():
        raise EmailSendError("SMTP 未配置")

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except Exception as e:
        logger.error(f"Email send failed (to={to}, subject={subject!r}): {e}")
        raise EmailSendError(f"邮件发送失败: {e}") from e


def build_activation_link(token: str) -> str:
    """构造前端激活页链接（前端路由 /activate-email 调 API 完成激活）。"""
    base = settings.email_verify_link_base.rstrip("/")
    return f"{base}/activate-email?token={token}"
