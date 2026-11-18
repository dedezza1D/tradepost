"""Mercado Pago adapter: Orders API (Checkout Transparente) with Pix and card.

The platform's account receives every payment; each seller's share is recorded in the ledger
and settled outside Mercado Pago. Native marketplace split needs every seller to connect their
own Mercado Pago account via OAuth and one payment per seller; it is left out on purpose (see
README).
"""

import hashlib
import hmac
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import httpx

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

API = "https://api.mercadopago.com"

_STATUS = {
    "created": AttemptStatus.PENDING,
    "processing": AttemptStatus.PENDING,
    "action_required": AttemptStatus.PENDING,
    "processed": AttemptStatus.SUCCEEDED,
    "failed": AttemptStatus.FAILED,
    "canceled": AttemptStatus.CANCELLED,
    "cancelled": AttemptStatus.CANCELLED,
    "expired": AttemptStatus.CANCELLED,
    "refunded": AttemptStatus.REFUNDED,
    "charged_back": AttemptStatus.REFUNDED,
}


def _amount(cents: int) -> str:
    return str((Decimal(cents) / 100).quantize(Decimal("0.01")))


class MercadoPagoProvider:
    name = "mercadopago"
    methods: tuple[Method, ...] = ("pix", "card")

    def __init__(
        self,
        access_token: str,
        public_key: str,
        webhook_secret: str | None,
        *,
        sandbox_auto_approve: bool,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._public_key = public_key
        self._webhook_secret = webhook_secret
        # In the sandbox nobody can scan a Pix QR code. Mercado Pago approves test Pix orders
        # whose payer first name is "APRO", which stands in for the buyer paying.
        self._sandbox_auto_approve = sandbox_auto_approve
        self._http = http or httpx.AsyncClient(
            base_url=API, timeout=20, headers={"Authorization": f"Bearer {access_token}"}
        )

    def public_config(self) -> dict[str, Any]:
        from tradepost.config import get_settings

        return {"public_key": self._public_key, "max_installments": get_settings().max_installments}

    async def is_sandbox(self) -> bool:
        """Mercado Pago test credentials look like live ones (``APP_USR-``); the account they
        belong to is what tells them apart. Only test users may be used here."""
        me = await self._request("GET", "/users/me")
        return "test_user" in (me.get("tags") or [])

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PaymentProviderError("Mercado Pago is unreachable.") from exc
        if response.status_code >= 400:
            raise PaymentProviderError(
                "Mercado Pago rejected the request.",
                provider_status=response.status_code,
            )
        data: dict[str, Any] = response.json() if response.content else {}
        return data

    @staticmethod
    def _result(order: dict[str, Any]) -> StatusResult:
        payments = (order.get("transactions") or {}).get("payments") or [{}]
        payment = payments[0]
        status = _STATUS.get(order.get("status", ""), AttemptStatus.PENDING)
        detail = payment.get("status_detail") or order.get("status_detail")
        return StatusResult(status=status, charge_ref=payment.get("id"), status_detail=detail)

    async def start(
        self, checkout: CheckoutRef, method: Method, card: CardData | None
    ) -> AttemptResult:
        amount = _amount(checkout.amount_cents)
        payer: dict[str, Any] = {"email": checkout.buyer_email}
        if method == "pix":
            payment_method: dict[str, Any] = {"id": "pix", "type": "bank_transfer"}
            if self._sandbox_auto_approve:
                payer["first_name"] = "APRO"
            key = f"mp-{checkout.checkout_id}-pix"
        elif method == "card" and card is not None:
            payment_method = {
                "id": card.payment_method_id,
                "type": "credit_card",
                "token": card.token,
                "installments": card.installments,
            }
            payer = {"email": card.payer_email}
            # A card token is single use, so every card attempt is a new order.
            token_hash = hashlib.sha256(card.token.encode()).hexdigest()[:16]
            key = f"mp-{checkout.checkout_id}-card-{token_hash}"
        else:
            raise PaymentProviderError("Card details are required.")
        order = await self._request(
            "POST",
            "/v1/orders",
            json={
                "type": "online",
                "processing_mode": "automatic",
                "total_amount": amount,
                "external_reference": checkout.checkout_id,
                "payer": payer,
                "transactions": {
                    "payments": [{"amount": amount, "payment_method": payment_method}]
                },
            },
            headers={"X-Idempotency-Key": key},
        )
        result = self._result(order)
        client_data: dict[str, Any] = {}
        if method == "pix":
            pm = ((order.get("transactions") or {}).get("payments") or [{}])[0].get(
                "payment_method"
            ) or {}
            client_data = {
                "qr_code": pm.get("qr_code"),
                "qr_code_base64": pm.get("qr_code_base64"),
                "ticket_url": pm.get("ticket_url"),
            }
        return AttemptResult(
            provider_ref=str(order["id"]),
            method=method,
            status=result.status,
            client_data=client_data,
            charge_ref=result.charge_ref,
            status_detail=result.status_detail,
        )

    async def get_status(self, provider_ref: str) -> StatusResult:
        return self._result(await self._request("GET", f"/v1/orders/{provider_ref}"))

    async def cancel(self, provider_ref: str) -> StatusResult:
        try:
            order = await self._request(
                "POST",
                f"/v1/orders/{provider_ref}/cancel",
                headers={"X-Idempotency-Key": f"cancel-{provider_ref}"},
            )
            return self._result(order)
        except PaymentProviderError:
            # Not cancellable any more (already paid, for instance): report its state.
            return await self.get_status(provider_ref)

    async def refund(self, provider_ref: str, amount_cents: int, idempotency_key: str) -> None:
        order = await self._request("GET", f"/v1/orders/{provider_ref}")
        payment = ((order.get("transactions") or {}).get("payments") or [{}])[0]
        total = Decimal(str(order.get("total_amount", "0")))
        body: dict[str, Any] | None = None
        if Decimal(amount_cents) / 100 < total:
            body = {"transactions": [{"id": payment.get("id"), "amount": _amount(amount_cents)}]}
        await self._request(
            "POST",
            f"/v1/orders/{provider_ref}/refund",
            json=body,
            headers={"X-Idempotency-Key": idempotency_key},
        )

    def verify_webhook(
        self, headers: Mapping[str, str], query: Mapping[str, str], body: bytes
    ) -> WebhookNotice | None:
        raw_id = query.get("data.id") or ""
        data_id = raw_id.lower()  # the signed manifest uses the lower-cased id
        if not data_id or query.get("type", "order") != "order":
            return None
        request_id = headers.get("x-request-id", "")
        parts = dict(
            p.strip().split("=", 1) for p in headers.get("x-signature", "").split(",") if "=" in p
        )
        ts = parts.get("ts", "")
        if self._webhook_secret:
            manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
            expected = hmac.new(
                self._webhook_secret.encode(), manifest.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, parts.get("v1", "")):
                raise InvalidWebhook("Invalid Mercado Pago signature.")
        # Without a secret (local development) the notice is still harmless: it only names an
        # order, whose status is then read from the API with our own credentials.
        return WebhookNotice(
            event_id=request_id or f"{data_id}:{ts}",
            event_type=query.get("action") or "order",
            provider_ref=raw_id,
        )
