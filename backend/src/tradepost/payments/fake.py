"""In-memory provider for tests and offline development.

It behaves like a real provider from the outside: payments start pending, change only when the
"provider side" says so, and announce changes through signed webhooks.
"""

import hashlib
import hmac
import itertools
import json
from collections.abc import Mapping
from typing import Any

from tradepost.orders.models import AttemptStatus
from tradepost.payments.base import (
    AttemptResult,
    CardData,
    CheckoutRef,
    InvalidWebhook,
    Method,
    StatusResult,
    WebhookNotice,
)

FAKE_WEBHOOK_SECRET = b"fake-webhook-secret"
APPROVED_TEST_TOKEN = "tok_approved"  # noqa: S105 - test double


def sign(body: bytes) -> str:
    return hmac.new(FAKE_WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()


class FakeProvider:
    name = "fake"
    methods: tuple[Method, ...] = ("pix", "card")

    def __init__(self) -> None:
        self.payments: dict[str, dict[str, Any]] = {}
        self.refunds: list[tuple[str, int]] = []
        self.transfers: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self.fail_next_start = False

    def public_config(self) -> dict[str, Any]:
        return {"methods": list(self.methods)}

    async def start(
        self, checkout: CheckoutRef, method: Method, card: CardData | None
    ) -> AttemptResult:
        if self.fail_next_start:
            self.fail_next_start = False
            from tradepost.payments.base import PaymentProviderError

            raise PaymentProviderError("Provider unavailable.")
        ref = f"fake_{next(self._ids)}"
        status = AttemptStatus.PENDING
        if method == "card" and card is not None:
            approved = card.token == APPROVED_TEST_TOKEN
            status = AttemptStatus.SUCCEEDED if approved else AttemptStatus.FAILED
        self.payments[ref] = {
            "status": status,
            "amount": checkout.amount_cents,
            "checkout_id": checkout.checkout_id,
        }
        client_data = {"pix_code": f"PIX-{ref}"} if method == "pix" else {}
        return AttemptResult(
            provider_ref=ref,
            method=method,
            status=status,
            client_data=client_data,
            charge_ref=f"ch_{ref}" if status == AttemptStatus.SUCCEEDED else None,
        )

    async def get_status(self, provider_ref: str) -> StatusResult:
        status = self.payments[provider_ref]["status"]
        return StatusResult(
            status=status, charge_ref=f"ch_{provider_ref}" if status == "succeeded" else None
        )

    async def cancel(self, provider_ref: str) -> StatusResult:
        payment = self.payments[provider_ref]
        if payment["status"] == AttemptStatus.PENDING:
            payment["status"] = AttemptStatus.CANCELLED
        return await self.get_status(provider_ref)

    async def refund(self, provider_ref: str, amount_cents: int, idempotency_key: str) -> None:
        self.refunds.append((provider_ref, amount_cents))

    def verify_webhook(
        self, headers: Mapping[str, str], query: Mapping[str, str], body: bytes
    ) -> WebhookNotice | None:
        if not hmac.compare_digest(headers.get("x-fake-signature", ""), sign(body)):
            raise InvalidWebhook("Bad signature.")
        data = json.loads(body)
        return WebhookNotice(
            event_id=data["id"], event_type=data["type"], provider_ref=data.get("payment")
        )

    # --- test helpers: what the provider side would do -------------------------------------

    def settle(self, provider_ref: str, status: AttemptStatus = AttemptStatus.SUCCEEDED) -> None:
        self.payments[provider_ref]["status"] = status

    # --- payouts --------------------------------------------------------------------------

    async def create_seller_account(self, *, email: str, display_name: str) -> str:
        return f"acct_fake_{next(self._ids)}"

    async def onboarding_link(self, account_id: str, return_url: str) -> str:
        return f"https://fake.example/onboard/{account_id}"

    async def transfer(
        self,
        *,
        account_id: str,
        amount_cents: int,
        currency: str,
        charge_ref: str | None,
        group: str,
        idempotency_key: str,
    ) -> str:
        self.transfers.append(
            {"account": account_id, "amount": amount_cents, "key": idempotency_key}
        )
        return f"tr_{idempotency_key}"
