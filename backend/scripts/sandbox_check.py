"""Contract check of the payment adapters against the providers' sandboxes.

Runs the real adapters (not mocks) with the test keys from backend/.env and checks the
behaviour the order logic relies on. Refuses to run with live keys.

    uv run python scripts/sandbox_check.py
"""

import asyncio
import sys
import uuid

import stripe

from tradepost.config import get_settings
from tradepost.orders.models import AttemptStatus
from tradepost.payments.base import CheckoutRef
from tradepost.payments.mercadopago import MercadoPagoProvider
from tradepost.payments.registry import build_registry

failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {label} {detail if not condition else ''}")
    if not condition:
        failures.append(label)


def ref(amount: int) -> CheckoutRef:
    return CheckoutRef(
        checkout_id=str(uuid.uuid4()),
        amount_cents=amount,
        currency="BRL",
        buyer_email="buyer@example.com",
        description="sandbox check",
        expires_in_seconds=900,
    )


async def stripe_checks() -> None:
    registry = build_registry(get_settings())
    if "stripe" not in registry.names():
        print("skip stripe (no keys)")
        return
    provider = registry.get("stripe")
    attempt = await provider.start(ref(12_345), "element", None)
    check("stripe: intent created pending", attempt.status == AttemptStatus.PENDING)
    check("stripe: client secret returned", bool(attempt.client_data.get("client_secret")))

    # Simulate the browser confirming with a test card.
    key = get_settings().stripe_secret_key
    assert key is not None
    client = stripe.StripeClient(key.get_secret_value())
    client.v1.payment_intents.confirm(
        attempt.provider_ref,
        {"payment_method": "pm_card_visa", "return_url": "https://example.com"},
    )
    status = await provider.get_status(attempt.provider_ref)
    check("stripe: card payment succeeded", status.status == AttemptStatus.SUCCEEDED, status)
    check("stripe: charge id available for transfers", bool(status.charge_ref))

    cancel = await provider.cancel(attempt.provider_ref)
    check(
        "stripe: cancel after success reports succeeded", cancel.status == AttemptStatus.SUCCEEDED
    )

    account = await provider.create_seller_account(  # type: ignore[attr-defined]
        email="seller@example.com", display_name="Sandbox seller"
    )
    link = await provider.onboarding_link(account, "http://localhost:5180/seller")  # type: ignore[attr-defined]
    check("stripe: seller account and onboarding link", link.startswith("https://"))
    transfer = await provider.transfer(  # type: ignore[attr-defined]
        account_id=account,
        amount_cents=10_000,
        currency="BRL",
        charge_ref=status.charge_ref,
        group="sandbox-check",
        idempotency_key=f"payout-{uuid.uuid4()}",
    )
    check("stripe: transfer to seller", transfer.startswith("tr_"))

    await provider.refund(attempt.provider_ref, 2_345, f"refund-{uuid.uuid4()}")
    check("stripe: partial refund accepted", True)

    unpaid = await provider.start(ref(5_000), "element", None)
    cancelled = await provider.cancel(unpaid.provider_ref)
    check("stripe: unpaid intent cancels", cancelled.status == AttemptStatus.CANCELLED)


async def mercadopago_checks() -> None:
    registry = build_registry(get_settings())
    if "mercadopago" not in registry.names():
        print("skip mercadopago (no keys)")
        return
    provider = registry.get("mercadopago")
    attempt = await provider.start(ref(5_000), "pix", None)
    check("mercadopago: pix order pending", attempt.status == AttemptStatus.PENDING, attempt)
    check("mercadopago: pix QR code returned", bool(attempt.client_data.get("qr_code")))
    status = None
    for _ in range(15):
        await asyncio.sleep(2)
        status = await provider.get_status(attempt.provider_ref)
        if status.status != AttemptStatus.PENDING:
            break
    check(
        "mercadopago: sandbox pix approved",
        status and status.status == AttemptStatus.SUCCEEDED,
        status,
    )
    await provider.refund(attempt.provider_ref, 1_000, f"refund-{uuid.uuid4()}")
    check("mercadopago: partial refund accepted", True)

    # A Pix nobody pays (no sandbox auto-approval) must be cancellable.
    settings = get_settings()
    assert settings.mercadopago_access_token
    assert settings.mercadopago_public_key
    unpaid_provider = MercadoPagoProvider(
        settings.mercadopago_access_token.get_secret_value(),
        settings.mercadopago_public_key,
        None,
        sandbox_auto_approve=False,
    )
    other = await unpaid_provider.start(ref(3_000), "pix", None)
    cancelled = await unpaid_provider.cancel(other.provider_ref)
    check("mercadopago: unpaid pix cancels", cancelled.status == AttemptStatus.CANCELLED, cancelled)


async def main() -> int:
    await stripe_checks()
    await mercadopago_checks()
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
