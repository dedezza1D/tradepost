"""What a notification says outside the app, in the reader's language.

Inside the app the web client renders notifications from ``kind`` and ``payload``; push messages
and e-mails are written here instead, from the same facts and with the same sentences, because
the reader may not have the app open. Kinds are grouped into the categories people turn on and
off in their preferences.
"""

from typing import Any
from urllib.parse import urlencode

LANGUAGES = ("pt-BR", "en")

# category -> kinds. Kinds outside every category (admin alerts) never leave the app.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "messages": ("new_message", "question_received", "question_answered", "dispute_message"),
    "offers": (
        "offer_received",
        "offer_countered",
        "offer_accepted",
        "offer_declined",
        "offer_expired",
    ),
    "orders": (
        "order_paid",
        "purchase_confirmed",
        "order_shipped",
        "order_delivered",
        "order_refunded",
        "payment_refunded",
        "payout_sent",
        "dispute_opened",
        "dispute_refunded",
        "dispute_seller_rejected",
        "dispute_rejected",
        "dispute_escalated",
        "dispute_cancelled",
    ),
    "saved": ("saved_search", "saved_price_drop", "saved_low_stock"),
    "activity": (
        "new_follower",
        "listing_saved",
        "review_received",
        "listing_approved",
        "listing_removed",
    ),
}
CATEGORY_OF = {kind: category for category, kinds in CATEGORIES.items() for kind in kinds}
# Defaults until a person changes them: push for what needs an answer or an action, e-mail only
# for what happens to money and orders.
PUSH_DEFAULT = {"messages": True, "offers": True, "orders": True, "saved": True, "activity": False}
EMAIL_DEFAULT = {
    "messages": False,
    "offers": False,
    "orders": True,
    "saved": False,
    "activity": False,
}
# E-mail is transactional: a sale, a payment, a shipment and a dispute. The rest is push only.
EMAIL_KINDS = frozenset(
    {
        "order_paid",
        "purchase_confirmed",
        "order_shipped",
        "order_refunded",
        "dispute_opened",
        "dispute_refunded",
        "dispute_seller_rejected",
        "dispute_rejected",
        "dispute_escalated",
    }
)

TITLES: dict[str, dict[str, str]] = {
    "pt-BR": {
        "messages": "Nova mensagem",
        "offers": "Proposta",
        "orders": "Pedido",
        "saved": "Itens que você acompanha",
        "activity": "tradepost",
        "dispute": "Disputa",
        "sale": "Você vendeu!",
    },
    "en": {
        "messages": "New message",
        "offers": "Offer",
        "orders": "Order",
        "saved": "Items you follow",
        "activity": "tradepost",
        "dispute": "Dispute",
        "sale": "You made a sale!",
    },
}

SENTENCES: dict[str, dict[str, str]] = {
    "pt-BR": {
        "order_paid": "Você vendeu {title} por {total}. Agora é só enviar.",
        "purchase_confirmed": "Pagamento confirmado: {title}.",
        "order_shipped": "{title} foi enviado. Rastreio: {tracking}.",
        "order_delivered": "O comprador recebeu {title}.",
        "order_refunded": "Você foi reembolsado por {title} ({total}).",
        "payment_refunded": "Um pagamento de {amount} foi devolvido a você.",
        "payout_sent": "Repasse de {amount} enviado por {title}.",
        "review_received": "Você recebeu uma avaliação de {rating} estrela(s) por {title}.",
        "new_follower": "{name} começou a seguir você.",
        "listing_saved": "{name} salvou {title}.",
        "saved_price_drop": "{title} baixou de {old} para {price}.",
        "saved_low_stock": "Só resta 1 unidade de {title}.",
        "listing_removed": "{title} foi removido pela moderação.",
        "listing_approved": "{title} foi aprovado e já está no ar.",
        "new_message": "{name} mandou mensagem sobre {title}.",
        "offer_received": "{name} ofereceu {offer} por {title}.",
        "offer_countered": "{name} fez uma contraproposta de {offer} por {title}.",
        "offer_accepted": "{name} aceitou {offer} por {title}.",
        "offer_declined": "{name} recusou {offer} por {title}.",
        "offer_expired": "Sua oferta de {offer} por {title} expirou.",
        "question_received": "{name} perguntou sobre {title}.",
        "question_answered": "O vendedor respondeu sua pergunta sobre {title}.",
        "dispute_opened": "O comprador abriu uma disputa sobre {title}.",
        "dispute_message": "Nova mensagem na disputa de {title}.",
        "dispute_refunded": "A disputa de {title} terminou em reembolso de {amount}.",
        "dispute_seller_rejected": (
            "O vendedor recusou a disputa de {title}. Você pode pedir que o tradepost decida."
        ),
        "dispute_rejected": "A disputa de {title} foi encerrada sem reembolso.",
        "dispute_escalated": "A disputa de {title} foi para a decisão do tradepost.",
        "dispute_cancelled": "O comprador encerrou a disputa de {title}.",
        "saved_search_one": "{count} anúncio novo para a sua busca {search}.",
        "saved_search_other": "{count} anúncios novos para a sua busca {search}.",
        "more_items_one": " e mais {count} item",
        "more_items_other": " e mais {count} itens",
    },
    "en": {
        "order_paid": "You sold {title} for {total}. Time to ship it.",
        "purchase_confirmed": "Payment confirmed: {title}.",
        "order_shipped": "{title} has shipped. Tracking: {tracking}.",
        "order_delivered": "The buyer received {title}.",
        "order_refunded": "You were refunded for {title} ({total}).",
        "payment_refunded": "A payment of {amount} was returned to you.",
        "payout_sent": "Payout of {amount} sent for {title}.",
        "review_received": "You got a {rating}-star review for {title}.",
        "new_follower": "{name} started following you.",
        "listing_saved": "{name} saved {title}.",
        "saved_price_drop": "{title} dropped from {old} to {price}.",
        "saved_low_stock": "Only 1 left of {title}.",
        "listing_removed": "{title} was removed by moderation.",
        "listing_approved": "{title} was approved and is now live.",
        "new_message": "{name} sent a message about {title}.",
        "offer_received": "{name} offered {offer} for {title}.",
        "offer_countered": "{name} countered with {offer} for {title}.",
        "offer_accepted": "{name} accepted {offer} for {title}.",
        "offer_declined": "{name} declined {offer} for {title}.",
        "offer_expired": "Your offer of {offer} for {title} expired.",
        "question_received": "{name} asked about {title}.",
        "question_answered": "The seller answered your question about {title}.",
        "dispute_opened": "The buyer opened a dispute about {title}.",
        "dispute_message": "New message in the dispute about {title}.",
        "dispute_refunded": "The dispute about {title} ended in a refund of {amount}.",
        "dispute_seller_rejected": (
            "The seller declined the dispute about {title}. You can ask tradepost to decide."
        ),
        "dispute_rejected": "The dispute about {title} was closed without a refund.",
        "dispute_escalated": "The dispute about {title} went to tradepost to decide.",
        "dispute_cancelled": "The buyer closed the dispute about {title}.",
        "saved_search_one": "{count} new listing for your search {search}.",
        "saved_search_other": "{count} new listings for your search {search}.",
        "more_items_one": " and {count} more item",
        "more_items_other": " and {count} more items",
    },
}

EMAIL: dict[str, dict[str, str]] = {
    "pt-BR": {
        "greeting": "Olá, {name}.",
        "open": "Abrir no tradepost",
        "why": (
            "Você recebe este e-mail porque ele trata de um pedido seu no tradepost, um "
            "marketplace de demonstração em que os pagamentos rodam em modo de teste."
        ),
        "preferences": "Escolha quais avisos receber",
    },
    "en": {
        "greeting": "Hi {name},",
        "open": "Open in tradepost",
        "why": (
            "You get this e-mail because it is about one of your orders on tradepost, a demo "
            "marketplace where payments run in test mode."
        ),
        "preferences": "Choose which notices you get",
    },
}

TEST_PUSH = {
    "pt-BR": "Os avisos estão funcionando neste aparelho.",
    "en": "Notifications are working on this device.",
}

SELLER_KINDS = frozenset({"order_paid", "order_delivered", "payout_sent", "review_received"})
SAVED_FIELDS = ("q", "category", "condition", "state", "min_price", "max_price")


def language(locale: str | None) -> str:
    return "en" if (locale or "").lower().startswith("en") else "pt-BR"


def money(cents: Any, lang: str) -> str:
    if not isinstance(cents, int):
        return ""
    whole, frac = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else ""
    if lang == "en":
        return f"{sign}R${whole:,}.{frac:02d}"
    return f"{sign}R$ {whole:,}".replace(",", ".") + f",{frac:02d}"


def _plural(lang: str, key: str, count: int) -> str:
    return SENTENCES[lang][f"{key}_{'one' if count == 1 else 'other'}"]


def _search_label(params: dict[str, Any]) -> str:
    q, category = params.get("q"), params.get("category")
    if q and category:
        return f"“{q}” · {category}"
    return f"“{q}”" if q else str(category or "")


def sentence(kind: str, payload: dict[str, Any], lang: str) -> str:
    more = int(payload.get("more_items") or 0)
    title = str(payload.get("title") or "")
    if more:
        title += _plural(lang, "more_items", more).format(count=more)
    if kind == "saved_search":
        count = int(payload.get("count") or 0)
        return _plural(lang, "saved_search", count).format(
            count=count, search=_search_label(payload.get("params") or {})
        )
    template = SENTENCES[lang].get(kind)
    if template is None:
        return ""
    return template.format(
        title=title,
        total=money(payload.get("total_cents"), lang),
        amount=money(payload.get("amount_cents"), lang),
        offer=money(payload.get("amount_cents"), lang),
        old=money(payload.get("old_price_cents"), lang),
        price=money(payload.get("price_cents"), lang),
        tracking=payload.get("tracking_code") or "",
        rating=payload.get("rating") or "",
        name=payload.get("name") or "",
    )


def title(kind: str, lang: str) -> str:
    titles = TITLES[lang]
    if kind == "order_paid":
        return titles["sale"]
    if kind.startswith("dispute_"):
        return titles["dispute"]
    return titles[CATEGORY_OF.get(kind, "activity")]


def target(kind: str, payload: dict[str, Any]) -> str:
    """Where opening the notice goes; the same rules as the web app's bell."""
    if payload.get("conversation_id"):
        return f"/inbox/{payload['conversation_id']}"
    if payload.get("dispute_id"):
        return f"/disputes/{payload['dispute_id']}"
    if kind == "saved_search" and payload.get("params"):
        params = payload["params"]
        query = {k: params[k] for k in SAVED_FIELDS if params.get(k) is not None}
        return f"/search?{urlencode({**query, 'sort': 'newest'})}"
    if kind == "new_follower" and payload.get("user_id"):
        return f"/users/{payload['user_id']}"
    if kind == "listing_removed":
        return "/me/listings"
    if payload.get("listing_id") and not payload.get("order_id"):
        return f"/listings/{payload['listing_id']}"
    if kind in SELLER_KINDS:
        return "/seller"
    if payload.get("order_id"):
        return "/orders"
    return "/notifications"
