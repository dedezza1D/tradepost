"""Webhook verification of the real adapters, with locally signed payloads (no network)."""

import hashlib
import hmac
import json
import time

import pytest

from tradepost.payments.base import InvalidWebhook
from tradepost.payments.mercadopago import MercadoPagoProvider
from tradepost.payments.stripe_provider import StripeProvider

STRIPE_SECRET = "whsec_unit_test_secret"


def stripe_signed(payload: dict[str, object], secret: str = STRIPE_SECRET) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, f"t={ts},v1={sig}"


def stripe_event(event_type: str, obj: dict[str, object]) -> dict[str, object]:
    return {
        "id": "evt_123",
        "object": "event",
        "type": event_type,
        "api_version": "2026-10-28",
        "created": int(time.time()),
        "data": {"object": obj},
    }


@pytest.fixture
def stripe_provider() -> StripeProvider:
    return StripeProvider("sk_test_unit", "pk_test_unit", STRIPE_SECRET)


def test_stripe_refuses_live_keys() -> None:
    with pytest.raises(ValueError, match="non-test"):
        StripeProvider("sk_live_nope", "pk_live_nope", None)


def test_stripe_payment_intent_event(stripe_provider: StripeProvider) -> None:
    body, sig = stripe_signed(
        stripe_event("payment_intent.succeeded", {"id": "pi_1", "object": "payment_intent"})
    )
    notice = stripe_provider.verify_webhook({"stripe-signature": sig}, {}, body)
    assert notice is not None
    assert (notice.event_id, notice.provider_ref) == ("evt_123", "pi_1")


def test_stripe_charge_event_points_to_its_payment_intent(stripe_provider: StripeProvider) -> None:
    body, sig = stripe_signed(
        stripe_event(
            "charge.refunded", {"id": "ch_1", "object": "charge", "payment_intent": "pi_9"}
        )
    )
    notice = stripe_provider.verify_webhook({"stripe-signature": sig}, {}, body)
    assert notice is not None
    assert notice.provider_ref == "pi_9"


def test_stripe_ignores_unrelated_events(stripe_provider: StripeProvider) -> None:
    body, sig = stripe_signed(
        stripe_event("customer.created", {"id": "cus_1", "object": "customer"})
    )
    assert stripe_provider.verify_webhook({"stripe-signature": sig}, {}, body) is None


def test_stripe_rejects_bad_signatures(stripe_provider: StripeProvider) -> None:
    body, sig = stripe_signed(
        stripe_event("payment_intent.succeeded", {"id": "pi_1", "object": "payment_intent"}),
        secret="whsec_someone_else",
    )
    with pytest.raises(InvalidWebhook):
        stripe_provider.verify_webhook({"stripe-signature": sig}, {}, body)


def mp(secret: str | None) -> MercadoPagoProvider:
    return MercadoPagoProvider("APP_USR-unit", "APP_USR-pub", secret, sandbox_auto_approve=True)


def test_mercadopago_signed_notification() -> None:
    secret = "mp-secret"
    ts, request_id, order_id = "1700000000", "req-1", "ORD01ABC"
    manifest = f"id:{order_id.lower()};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    headers = {"x-signature": f"ts={ts},v1={v1}", "x-request-id": request_id}
    notice = mp(secret).verify_webhook(headers, {"data.id": order_id, "type": "order"}, b"{}")
    assert notice is not None
    assert (notice.event_id, notice.provider_ref) == (request_id, order_id)

    forged = {"x-signature": f"ts={ts},v1={'0' * 64}", "x-request-id": request_id}
    with pytest.raises(InvalidWebhook):
        mp(secret).verify_webhook(forged, {"data.id": order_id, "type": "order"}, b"{}")


def test_mercadopago_ignores_other_topics() -> None:
    assert mp(None).verify_webhook({}, {"data.id": "1", "type": "payment"}, b"{}") is None
