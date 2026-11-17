"""The port every payment provider adapter implements.

The rest of the system (checkout, orders, ledger, jobs) only sees these types, so adding a
provider never touches order logic. Provider-specific statuses are mapped to
``AttemptStatus`` inside each adapter.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from tradepost.errors import AppError
from tradepost.orders.models import AttemptStatus

Method = Literal["element", "pix", "card"]


class PaymentProviderError(AppError):
    status_code = 502
    code = "payment_provider_error"


class InvalidWebhook(AppError):
    status_code = 400
    code = "invalid_webhook"


@dataclass(frozen=True)
class CheckoutRef:
    """What an adapter needs to know about the checkout being paid."""

    checkout_id: str
    amount_cents: int
    currency: str
    buyer_email: str
    description: str
    expires_in_seconds: int


@dataclass(frozen=True)
class CardData:
    """A card token produced in the browser by the provider's own widget. Raw card numbers never
    reach this service."""

    token: str
    payment_method_id: str
    installments: int
    payer_email: str
    issuer_id: str | None = None


@dataclass(frozen=True)
class AttemptResult:
    provider_ref: str
    method: str
    status: AttemptStatus
    client_data: dict[str, Any] = field(default_factory=dict)
    charge_ref: str | None = None
    status_detail: str | None = None


@dataclass(frozen=True)
class StatusResult:
    status: AttemptStatus
    charge_ref: str | None = None
    status_detail: str | None = None


@dataclass(frozen=True)
class WebhookNotice:
    """A verified notification. It only says *which* payment changed: the new status is always
    fetched from the provider's API, so a forged or stale payload cannot mark anything paid."""

    event_id: str
    event_type: str
    provider_ref: str | None


class PaymentProvider(Protocol):
    name: str
    methods: tuple[Method, ...]

    def public_config(self) -> dict[str, Any]: ...

    async def start(
        self, checkout: CheckoutRef, method: Method, card: CardData | None
    ) -> AttemptResult: ...

    async def get_status(self, provider_ref: str) -> StatusResult: ...

    async def cancel(self, provider_ref: str) -> StatusResult: ...

    async def refund(self, provider_ref: str, amount_cents: int, idempotency_key: str) -> None: ...

    def verify_webhook(
        self, headers: Mapping[str, str], query: Mapping[str, str], body: bytes
    ) -> WebhookNotice | None: ...


class PayoutProvider(Protocol):
    """Providers that can move a seller's share to the seller's own account."""

    async def create_seller_account(self, *, email: str, display_name: str) -> str: ...

    async def onboarding_link(self, account_id: str, return_url: str) -> str: ...

    async def transfer(
        self,
        *,
        account_id: str,
        amount_cents: int,
        currency: str,
        charge_ref: str | None,
        group: str,
        idempotency_key: str,
    ) -> str: ...


class PaymentRegistry:
    def __init__(self, providers: list[PaymentProvider]) -> None:
        self._providers = {p.name: p for p in providers}

    def get(self, name: str) -> PaymentProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise AppError("This payment provider is not available.", code="unknown_provider")
        return provider

    def names(self) -> list[str]:
        return list(self._providers)

    def all(self) -> list[PaymentProvider]:
        return list(self._providers.values())
