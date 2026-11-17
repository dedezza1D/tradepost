from tradepost.config import Settings
from tradepost.logs import get_logger
from tradepost.payments.base import PaymentProvider, PaymentRegistry

log = get_logger(__name__)


def _secret(value: object) -> str | None:
    getter = getattr(value, "get_secret_value", None)
    secret = getter() if getter else None
    return secret or None


def build_registry(settings: Settings) -> PaymentRegistry:
    providers: list[PaymentProvider] = []
    for name in settings.payment_providers:
        if name == "stripe":
            key = _secret(settings.stripe_secret_key)
            if key and settings.stripe_publishable_key:
                from tradepost.payments.stripe_provider import StripeProvider

                providers.append(
                    StripeProvider(
                        key,
                        settings.stripe_publishable_key,
                        _secret(settings.stripe_webhook_secret),
                    )
                )
                continue
        elif name == "mercadopago":
            token = _secret(settings.mercadopago_access_token)
            if token and settings.mercadopago_public_key:
                from tradepost.payments.mercadopago import MercadoPagoProvider

                providers.append(
                    MercadoPagoProvider(
                        token,
                        settings.mercadopago_public_key,
                        _secret(settings.mercadopago_webhook_secret),
                        sandbox_auto_approve=settings.payments_sandbox,
                    )
                )
                continue
        elif name == "fake":
            from tradepost.payments.fake import FakeProvider

            providers.append(FakeProvider())
            continue
        log.warning("payment_provider_disabled", provider=name, reason="missing credentials")
    return PaymentRegistry(providers)


async def drop_live_accounts(registry: PaymentRegistry) -> PaymentRegistry:
    """Remove any provider whose credentials turn out not to be a sandbox account."""
    kept: list[PaymentProvider] = []
    for provider in registry.all():
        check = getattr(provider, "is_sandbox", None)
        try:
            sandbox = await check() if check else True
        except Exception:
            log.exception("payment_provider_check_failed", provider=provider.name)
            sandbox = False
        if sandbox:
            kept.append(provider)
        else:
            log.error(
                "payment_provider_disabled", provider=provider.name, reason="not a sandbox account"
            )
    return PaymentRegistry(kept)
