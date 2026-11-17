"""Stripe adapter: PaymentIntents (card and Pix through the Payment Element) and Connect.

Money flow is *separate charges and transfers*: the platform charges the buyer once for the
whole checkout (which may span several sellers) and later transfers each seller's share to
their connected account, keeping the commission.
"""

from collections.abc import Mapping
from typing import Any

import stripe
from stripe.params import TransferCreateParams

from tradepost.orders.models import AttemptStatus
from tradepost.payments.base import (
    AttemptResult,
    CardData,
    CheckoutRef,
    InvalidWebhook,
    Method,
    PaymentProviderError,
    StatusResult,
    WebhookNotice,
)

_STATUS = {
    "succeeded": AttemptStatus.SUCCEEDED,
    "canceled": AttemptStatus.CANCELLED,
    # requires_payment_method also follows a declined card: the buyer can retry on the same
    # PaymentIntent, so it stays pending until the checkout expires.
    "requires_payment_method": AttemptStatus.PENDING,
    "requires_confirmation": AttemptStatus.PENDING,
    "requires_action": AttemptStatus.PENDING,
    "processing": AttemptStatus.PENDING,
    "requires_capture": AttemptStatus.PENDING,
}

_PAYMENT_EVENTS = (
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "payment_intent.canceled",
    "payment_intent.processing",
    "payment_intent.requires_action",
    "charge.refunded",
)

PIX_MAX_EXPIRY_SECONDS = 86_400


class StripeProvider:
    name = "stripe"
    methods: tuple[Method, ...] = ("element",)

    def __init__(self, secret_key: str, publishable_key: str, webhook_secret: str | None) -> None:
        if not secret_key.startswith(("sk_test_", "rk_test_")):
            # This project only ever runs against the sandbox.
            raise ValueError("Stripe is configured with a non-test key")
        self._client = stripe.StripeClient(secret_key, http_client=stripe.HTTPXClient())
        self._publishable_key = publishable_key
        self._webhook_secret = webhook_secret

    def public_config(self) -> dict[str, Any]:
        return {"publishable_key": self._publishable_key}

    @staticmethod
    def _result(pi: Any) -> StatusResult:
        status = _STATUS.get(pi.status, AttemptStatus.PENDING)
        charge = pi.latest_charge
        charge_id = charge if isinstance(charge, str) else getattr(charge, "id", None)
        error = getattr(pi, "last_payment_error", None)
        detail = getattr(error, "code", None) if error else pi.status
        return StatusResult(status=status, charge_ref=charge_id, status_detail=detail)

    async def start(
        self, checkout: CheckoutRef, method: Method, card: CardData | None
    ) -> AttemptResult:
        try:
            pi = await self._client.v1.payment_intents.create_async(
                {
                    "amount": checkout.amount_cents,
                    "currency": checkout.currency.lower(),
                    "automatic_payment_methods": {"enabled": True},
                    "description": checkout.description,
                    "metadata": {"checkout_id": checkout.checkout_id},
                    "transfer_group": checkout.checkout_id,
                    "payment_method_options": {
                        "pix": {
                            "expires_after_seconds": min(
                                checkout.expires_in_seconds, PIX_MAX_EXPIRY_SECONDS
                            )
                        }
                    },
                },
                {"idempotency_key": f"pi-{checkout.checkout_id}"},
            )
        except stripe.StripeError as exc:
            raise PaymentProviderError("Could not start the payment.") from exc
        result = self._result(pi)
        return AttemptResult(
            provider_ref=pi.id,
            method=method,
            status=result.status,
            client_data={"client_secret": pi.client_secret},
            charge_ref=result.charge_ref,
            status_detail=result.status_detail,
        )

    async def get_status(self, provider_ref: str) -> StatusResult:
        try:
            pi = await self._client.v1.payment_intents.retrieve_async(provider_ref)
        except stripe.StripeError as exc:
            raise PaymentProviderError("Could not read the payment.") from exc
        return self._result(pi)

    async def cancel(self, provider_ref: str) -> StatusResult:
        try:
            pi = await self._client.v1.payment_intents.cancel_async(provider_ref)
            return self._result(pi)
        except stripe.InvalidRequestError:
            # Already succeeded or canceled: report what it is now.
            return await self.get_status(provider_ref)

    async def refund(self, provider_ref: str, amount_cents: int, idempotency_key: str) -> None:
        try:
            await self._client.v1.refunds.create_async(
                {"payment_intent": provider_ref, "amount": amount_cents},
                {"idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            raise PaymentProviderError("Could not refund the payment.") from exc

    def verify_webhook(
        self, headers: Mapping[str, str], query: Mapping[str, str], body: bytes
    ) -> WebhookNotice | None:
        if not self._webhook_secret:
            raise InvalidWebhook("Webhook secret not configured.")
        try:
            event = stripe.Webhook.construct_event(
                body, headers.get("stripe-signature", ""), self._webhook_secret
            )
        except (ValueError, stripe.SignatureVerificationError):
            raise InvalidWebhook("Invalid Stripe signature.") from None
        if event.type not in _PAYMENT_EVENTS:
            return None
        obj = event.data.object.to_dict()
        ref = obj.get("payment_intent") if event.type.startswith("charge.") else obj.get("id")
        return WebhookNotice(event_id=event.id, event_type=event.type, provider_ref=ref)

    # --- Connect ------------------------------------------------------------------------

    async def create_seller_account(self, *, email: str, display_name: str) -> str:
        try:
            account = await self._client.v2.core.accounts.create_async(
                {
                    "display_name": display_name,
                    "contact_email": email,
                    "identity": {"country": "BR"},
                    "dashboard": "full",
                    "defaults": {
                        "responsibilities": {
                            "losses_collector": "stripe",
                            "fees_collector": "stripe",
                        }
                    },
                    "configuration": {
                        "merchant": {"capabilities": {"card_payments": {"requested": True}}},
                        "recipient": {
                            "capabilities": {
                                "stripe_balance": {"stripe_transfers": {"requested": True}}
                            }
                        },
                    },
                }
            )
        except stripe.StripeError as exc:
            raise PaymentProviderError("Could not create the seller account.") from exc
        return str(account.id)

    async def onboarding_link(self, account_id: str, return_url: str) -> str:
        try:
            link = await self._client.v2.core.account_links.create_async(
                {
                    "account": account_id,
                    "use_case": {
                        "type": "account_onboarding",
                        "account_onboarding": {
                            "refresh_url": return_url,
                            "return_url": return_url,
                        },
                    },
                }
            )
        except stripe.StripeError as exc:
            raise PaymentProviderError("Could not create the onboarding link.") from exc
        return str(link.url)

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
        params: TransferCreateParams = {
            "amount": amount_cents,
            "currency": currency.lower(),
            "destination": account_id,
        }
        if charge_ref:
            # Ties the transfer to the charge, so it can happen before the funds settle. The
            # charge already carries the checkout's transfer group.
            params["source_transaction"] = charge_ref
        else:
            params["transfer_group"] = group
        try:
            transfer = await self._client.v1.transfers.create_async(
                params, {"idempotency_key": idempotency_key}
            )
        except stripe.StripeError as exc:
            raise PaymentProviderError("Could not pay out to the seller.") from exc
        return str(transfer.id)
