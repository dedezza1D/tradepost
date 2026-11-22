"""Transactional e-mail: a sale, a payment, a shipment, a dispute.

Any SMTP service works (most have a free tier); nothing here is tied to one provider. Without
one configured (``TP_EMAIL_PROVIDER=none``) e-mails are not sent, and ``log`` writes them to
the log instead, for development.
"""

import asyncio
import html
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from tradepost.config import Settings
from tradepost.logs import get_logger
from tradepost.notifications import texts

log = get_logger(__name__)


# Inline styles and tables: what e-mail clients render reliably.
_PAGE = "margin:0;background:#fbf6f2;font-family:Arial,Helvetica,sans-serif;color:#24130f"
_CARD = "max-width:520px;background:#ffffff;border-radius:16px"
_BAND = (
    "background:#c3102b;border-radius:16px 16px 0 0;padding:16px 24px;color:#ffffff;"
    "font-size:20px;font-weight:bold"
)
_BUTTON = (
    "display:inline-block;background:#c8102e;color:#ffffff;text-decoration:none;"
    "font-weight:bold;padding:12px 20px;border-radius:999px"
)
_FOOT = "padding:0 24px 24px;font-size:12px;line-height:1.5;color:#6b554d"
_TABLE = 'role="presentation" width="100%" cellpadding="0" cellspacing="0"'
HTML = "\n".join(
    [
        "<!doctype html>",
        f'<html lang="{{lang}}"><body style="{_PAGE}">',
        f'<table {_TABLE}><tr><td align="center" style="padding:24px 12px">',
        f'<table {_TABLE} style="{_CARD}">',
        f'<tr><td style="{_BAND}">tradepost</td></tr>',
        '<tr><td style="padding:24px">',
        '<p style="margin:0 0 12px">{greeting}</p>',
        '<p style="margin:0 0 20px;font-size:17px;line-height:1.45">{line}</p>',
        f'<a href="{{link}}" style="{_BUTTON}">{{open}}</a>',
        "</td></tr>",
        f'<tr><td style="{_FOOT}">',
        '{why}<br><a href="{preferences_link}" style="color:#6b554d">{preferences}</a>',
        "</td></tr></table></td></tr></table></body></html>",
    ]
)


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str


def compose(
    *,
    to: str,
    name: str,
    kind: str,
    payload: dict[str, object],
    locale: str | None,
    origin: str,
) -> Email:
    lang = texts.language(locale)
    words = texts.EMAIL[lang]
    line = texts.sentence(kind, dict(payload), lang)
    link = origin.rstrip("/") + texts.target(kind, dict(payload))
    preferences = origin.rstrip("/") + "/me#notifications"
    subject = line[:180]
    greeting = words["greeting"].format(name=name)
    text = (
        f"{greeting}\n\n{line}\n\n{words['open']}: {link}\n\n--\n{words['why']}\n"
        f"{words['preferences']}: {preferences}\n"
    )
    e = html.escape
    body = HTML.format(
        lang=lang,
        greeting=e(greeting),
        line=e(line),
        link=e(link),
        open=e(words["open"]),
        why=e(words["why"]),
        preferences_link=e(preferences),
        preferences=e(words["preferences"]),
    )
    return Email(to=to, subject=subject, text=text, html=body)


class EmailFailed(Exception):
    pass


class EmailSender:
    enabled = True

    async def send(self, message: Email) -> None:
        raise NotImplementedError


class NoEmail(EmailSender):
    enabled = False

    async def send(self, message: Email) -> None:  # pragma: no cover - never called
        return None


@dataclass
class LogEmail(EmailSender):
    """Development and tests: the e-mail goes to the log (and to ``sent``), never out."""

    sent: list[Email] = field(default_factory=list)

    async def send(self, message: Email) -> None:
        self.sent.append(message)
        log.info("email_logged", subject=message.subject)


class SmtpEmail(EmailSender):
    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host:
            raise ValueError("TP_SMTP_HOST is required for TP_EMAIL_PROVIDER=smtp")
        self.settings = settings

    def _send_blocking(self, message: Email) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["From"] = formataddr(("tradepost", s.email_from))
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg["Message-ID"] = make_msgid(domain=s.email_from.split("@")[-1])
        msg.set_content(message.text)
        msg.add_alternative(message.html, subtype="html")
        context = ssl.create_default_context()
        password = s.smtp_password.get_secret_value() if s.smtp_password else None
        assert s.smtp_host is not None
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=15) as smtp:
                if s.smtp_username and password:
                    smtp.login(s.smtp_username, password)
                smtp.send_message(msg)
            return
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls(context=context)
            if s.smtp_username and password:
                smtp.login(s.smtp_username, password)
            smtp.send_message(msg)

    async def send(self, message: Email) -> None:
        try:
            await asyncio.to_thread(self._send_blocking, message)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailFailed(type(exc).__name__) from exc


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_provider == "smtp":
        return SmtpEmail(settings)
    if settings.email_provider == "log":
        return LogEmail()
    return NoEmail()
