"""Flags messages that try to move the deal off the platform.

This only warns (both people see a notice next to the message); it never blocks. Buyers lose
the payment protection when they pay outside tradepost, and that is the main scam pattern in
classifieds, so the warning matters more than catching every variant.
"""

import re

OFF_PLATFORM_PAYMENT = "off_platform_payment"
CONTACT = "contact"

_PAYMENT = re.compile(
    r"\b(pix|picpay|pic\s*pay|transfer[eê]ncia|dep[oó]sito|boleto|ted|doc|bank\s*transfer|wire)\b"
    r"|https?://|www\.|\b[\w-]+\.(com|com\.br|net|link|ly|me)/",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# Brazilian phone numbers: optional +55, area code, 8 or 9 digits, common separators. Prices
# ("R$ 1.250,00") and years have too few digits to match.
_PHONE = re.compile(r"(?<!\d)(?:\+?55[\s.-]?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)")
_DIGITS_SPELLED = re.compile(r"(?:\d[\s.-]?){10,}")


def warning_for(text: str) -> str | None:
    if _PAYMENT.search(text):
        return OFF_PLATFORM_PAYMENT
    if _EMAIL.search(text) or _PHONE.search(text) or _DIGITS_SPELLED.search(text):
        return CONTACT
    return None
