"""Listing assistant: a draft from photos, a price range from similar listings, and tips.

Only the draft needs a model (a vision model reads the photos). The price range and the tips are
computed from the catalog, so they also work on the live demo, which runs without a model:

- **Draft**: one structured call (JSON schema) with the photos and a short guide to the
  categories. The answer is checked before it reaches the form; claims a photo cannot support
  ("funcionando perfeitamente") are removed, because only the seller knows them. Nothing is
  published from a draft: the seller reviews every field.
- **Price**: the asking prices of the most similar listings (by meaning with a model, by words
  without one), trimmed of outliers, as a range with the count it came from. Too few similar
  listings fall back to the category, and the answer says so.
- **Tips**: checks on the seller's own listing (few or dark photos, a vague title, a short
  description, a price far above similar listings).

The model is asked once per draft and does not call tools: the catalog lookups that would be
tools (categories, similar listings) are done by the server around the call, which a small local
model handles far more reliably than multi-step tool use.
"""

import asyncio
import re
import statistics
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Float, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.ai.client import AI, AIUnavailable
from tradepost.catalog.models import Listing, ListingPhoto, ListingStatus
from tradepost.catalog.search import text_match
from tradepost.catalog.semantic import ListingEmbedding, distance, prefixed
from tradepost.catalog.service import get_owned_listing
from tradepost.config import get_settings
from tradepost.deps import DbSession
from tradepost.errors import AppError
from tradepost.identity.sessions import CurrentUser
from tradepost.media.images import brightness, model_input
from tradepost.media.storage import Storage
from tradepost.security.ratelimit import rate_limit

FEATURE_DRAFT = "listing_draft"
FEATURE_PRICE = "price_lookup"
MAX_PHOTOS = 3
# The draft is asked again with these repeat penalties when the model loops (see below).
RETRY_PENALTIES: tuple[float | None, ...] = (None, 1.15, 1.3)
SIMILAR_FOR_PRICE = 20
MIN_SIMILAR = 3

# What each category holds, in the words a model reading a photo needs to tell them apart.
CATEGORY_GUIDE: dict[str, str] = {
    "electronics": "TVs, câmeras fotográficas, fones de ouvido, caixas de som",
    "phones": "celulares, smartphones e tablets",
    "computers": "notebooks, computadores, monitores, teclados",
    "games": "videogames, consoles, controles e jogos",
    "home": "utensílios de cozinha e casa, air fryer, liquidificador, panelas, decoração",
    "furniture": "móveis: sofás, mesas, cadeiras, estantes, camas",
    "appliances": "eletrodomésticos grandes: geladeira, fogão, máquina de lavar, micro-ondas",
    "fashion": "roupas, bolsas e acessórios",
    "shoes": "calçados: tênis, sapatos, sandálias, botas",
    "beauty": "beleza e cuidados pessoais",
    "sports": "artigos esportivos e de academia (bicicletas vão em bikes)",
    "bikes": "bicicletas e acessórios de bicicleta",
    "toys": "brinquedos",
    "baby": "artigos de bebê: carrinhos, berços, cadeirinhas",
    "books": "livros, revistas e quadrinhos",
    "music": "instrumentos musicais: violões, guitarras, teclados",
}

# The names the model chooses from. The first version offered the English slugs, and the model
# filed every camera and headphone under "phones" (it read "fone de ouvido" as phones); the
# evaluation compares both.
CATEGORY_LABELS: dict[str, str] = {
    "electronics": "eletrônicos",
    "phones": "celulares e tablets",
    "computers": "informática",
    "games": "videogames",
    "home": "casa e cozinha",
    "furniture": "móveis",
    "appliances": "eletrodomésticos grandes",
    "fashion": "moda",
    "shoes": "calçados",
    "beauty": "beleza",
    "sports": "esportes",
    "bikes": "bicicletas",
    "toys": "brinquedos",
    "baby": "bebê",
    "books": "livros",
    "music": "instrumentos musicais",
}


@dataclass(frozen=True)
class DraftPrompt:
    """How the draft is asked: category names in Portuguese or as slugs, with or without the
    guide to what each category holds. The API uses ``PROMPT``; the others are for the eval."""

    labels: bool = True
    guide: bool = True

    def name(self, slug: str) -> str:
        return CATEGORY_LABELS[slug] if self.labels else slug

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "is_product": {"type": "boolean"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "category": {"type": "string", "enum": [self.name(s) for s in CATEGORY_GUIDE]},
                "condition": {"type": "string", "enum": ["new", "used"]},
                "brand": {"type": "string"},
                "color": {"type": "string"},
            },
            "required": [
                "is_product",
                "title",
                "description",
                "category",
                "condition",
                "brand",
                "color",
            ],
        }

    @property
    def system(self) -> str:
        if self.guide:
            categories = "\n".join(
                f"  {self.name(slug)}: {text}" for slug, text in CATEGORY_GUIDE.items()
            )
        else:
            categories = "  " + ", ".join(self.name(slug) for slug in CATEGORY_GUIDE)
        return (
            "Você ajuda pessoas a anunciar itens num marketplace brasileiro. Olhe as fotos e "
            "escreva um rascunho de anúncio em português do Brasil.\n"
            "- title: curto e específico, até 60 caracteres: o tipo de produto, a marca e o "
            "modelo só se estiverem legíveis na foto, e uma característica visível (cor, tamanho "
            "aparente, material).\n"
            "- description: 2 a 3 frases só com o que a foto mostra (cor, material, partes, "
            "acessórios visíveis). Nunca afirme estado de conservação, funcionamento, tempo de "
            "uso, defeitos ou garantia: só quem vende sabe disso.\n"
            "- category: uma destas categorias:\n"
            f"{categories}\n"
            "- condition: new só se o item aparece na embalagem lacrada; senão used.\n"
            "- brand: a marca legível na foto, ou vazio. color: a cor principal, em português.\n"
            "- is_product: false se as fotos não mostram um item à venda (paisagem, pessoa, "
            "documento).\n"
            "Não invente marca, modelo, capacidade ou tamanho que não estejam visíveis."
        )

    def slug(self, answer: Any) -> str | None:
        for slug in CATEGORY_GUIDE:
            if answer == self.name(slug):
                return slug
        return None


PROMPT = DraftPrompt()

# Claims a photo cannot support. A sentence with one of them is removed from the draft.
UNSUPPORTED = re.compile(
    r"\b(funcion\w*|ótimo estado|otimo estado|bom estado|perfeito estado|excelente estado|"
    r"estado de novo|semi[- ]?novo|seminov\w*|pouco us\w*|nunca usad\w*|sem defeito\w*|"
    r"garantia|revisad\w*|perfeit\w* para)\b",
    re.IGNORECASE,
)


class Draft(BaseModel):
    title: str
    description: str
    category_slug: str | None
    condition: Literal["new", "used"]
    brand: str
    color: str
    # Sentences dropped from the model's description (shown to nobody; counted by the eval).
    removed_claims: int = 0


class PriceSuggestion(BaseModel):
    low_cents: int
    high_cents: int
    median_cents: int
    count: int
    basis: Literal["similar", "category"]


class Tip(BaseModel):
    code: Literal[
        "few_photos",
        "dark_photo",
        "short_title",
        "shouting_title",
        "short_description",
        "price_above_similar",
    ]
    # Numbers the app shows with the tip (e.g. the range the price is compared with).
    data: dict[str, int] = {}


class NotAProduct(AppError):
    status_code = 422
    code = "not_a_product"


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def clean_draft(raw: dict[str, Any], prompt: DraftPrompt = PROMPT) -> Draft:
    """Check the model's answer before it reaches the form."""
    if not raw.get("is_product", True):
        raise NotAProduct("The photos do not seem to show an item for sale.")
    kept = [s for s in _sentences(str(raw.get("description", ""))) if not UNSUPPORTED.search(s)]
    total = len(_sentences(str(raw.get("description", ""))))
    category = prompt.slug(raw.get("category"))
    title = " ".join(str(raw.get("title", "")).split())[:120]
    if UNSUPPORTED.search(title):
        title = UNSUPPORTED.sub("", title).strip(" ,-")
    return Draft(
        title=title,
        description=" ".join(kept)[:2000],
        category_slug=category,
        condition="new" if raw.get("condition") == "new" else "used",
        brand=str(raw.get("brand", "")).strip()[:60],
        color=str(raw.get("color", "")).strip().lower()[:30],
        removed_claims=total - len(kept),
    )


async def draft_from_photos(
    db: AsyncSession,
    ai: AI,
    photos: list[bytes],
    *,
    prompt: DraftPrompt = PROMPT,
    cache: bool = True,
) -> Draft:
    """A listing draft from up to three photos (already validated and resized).

    A small model sometimes repeats itself on a photo until the server aborts the answer; the
    same request would fail the same way (sampling warmer does not help either), so the retry
    penalises repeated tokens, harder on the last try (1.15 sometimes still loops)."""
    raw: Any = None
    for repeat_penalty in RETRY_PENALTIES:
        try:
            raw = await ai.complete_json(
                db,
                FEATURE_DRAFT,
                prompt.system,
                "Escreva o rascunho do anúncio para estas fotos.",
                prompt.schema,
                images=photos[:MAX_PHOTOS],
                max_tokens=400,
                repeat_penalty=repeat_penalty,
                cache=cache,
            )
            break
        except AIUnavailable:
            if repeat_penalty == RETRY_PENALTIES[-1] or not await ai.available(db):
                raise
    if not isinstance(raw, dict):
        raise AIUnavailable("The AI assistant did not answer. Try again.")
    return clean_draft(raw, prompt)


# --- price ----------------------------------------------------------------------------------------

PRICED = [ListingStatus.ACTIVE, ListingStatus.SOLD_OUT, ListingStatus.PAUSED]


async def _similar_prices(
    db: AsyncSession,
    ai: AI | None,
    text: str,
    category: str | None,
    exclude: uuid.UUID | None,
) -> list[int]:
    clauses: list[ColumnElement[bool]] = [Listing.status.in_(PRICED)]
    if category:
        clauses.append(Listing.category_slug == category)
    if exclude is not None:
        clauses.append(Listing.id != exclude)
    if ai is not None and await ai.available(db):
        try:
            model = ai.settings.ai_embed_model
            vector = (await ai.embed(db, FEATURE_PRICE, prefixed(model, [text], query=False)))[0]
        except AIUnavailable:
            vector = None
        if vector is not None:
            dist = distance(vector)
            rows = await db.scalars(
                select(Listing.price_cents)
                .join(
                    ListingEmbedding,
                    (ListingEmbedding.listing_id == Listing.id) & (ListingEmbedding.model == model),
                )
                .where(*clauses, dist <= ai.settings.search_max_distance)
                .order_by(dist)
                .limit(SIMILAR_FOR_PRICE)
            )
            return list(rows)
    matches, rank = text_match(text[:100])
    rows = await db.scalars(
        select(Listing.price_cents)
        .where(*clauses, matches)
        .order_by(rank.cast(Float).desc())
        .limit(SIMILAR_FOR_PRICE)
    )
    return list(rows)


def price_range(prices: list[int]) -> tuple[int, int, int]:
    """Interquartile range and median, after dropping prices far outside it (a typo with an
    extra zero should not move the suggestion)."""
    ordered = sorted(prices)
    if len(ordered) >= 4:
        q1, _, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
        spread = q3 - q1
        ordered = [p for p in ordered if q1 - 1.5 * spread <= p <= q3 + 1.5 * spread]
    if len(ordered) >= 4:
        low, median, high = statistics.quantiles(ordered, n=4, method="inclusive")
    else:
        low, median, high = ordered[0], statistics.median(ordered), ordered[-1]
    return int(round(low, -2)), int(round(median, -2)), int(round(high, -2))


async def suggest_price(
    db: AsyncSession,
    ai: AI | None,
    *,
    title: str,
    description: str = "",
    category: str | None = None,
    exclude: uuid.UUID | None = None,
) -> PriceSuggestion | None:
    text = f"{title}. {description}".strip(". ")
    prices = await _similar_prices(db, ai, text, category, exclude) if title.strip() else []
    basis: Literal["similar", "category"] = "similar"
    if len(prices) < MIN_SIMILAR and category:
        basis = "category"
        clauses = [Listing.status.in_(PRICED), Listing.category_slug == category]
        if exclude is not None:
            clauses.append(Listing.id != exclude)
        prices = list(await db.scalars(select(Listing.price_cents).where(*clauses).limit(200)))
    if len(prices) < MIN_SIMILAR:
        return None
    low, median, high = price_range(prices)
    return PriceSuggestion(
        low_cents=low, high_cents=high, median_cents=median, count=len(prices), basis=basis
    )


# --- tips -----------------------------------------------------------------------------------------

DARK = 0.22


async def listing_tips(
    db: AsyncSession, ai: AI | None, storage: Storage, listing: Listing
) -> list[Tip]:
    tips: list[Tip] = []
    photos = list(
        await db.scalars(
            select(ListingPhoto)
            .where(ListingPhoto.listing_id == listing.id)
            .order_by(ListingPhoto.position)
        )
    )
    if len(photos) < 3:
        tips.append(Tip(code="few_photos", data={"count": len(photos)}))
    if photos:
        thumb = await storage.get(photos[0].key_thumb)
        if thumb is not None and await asyncio.to_thread(brightness, thumb) < DARK:
            tips.append(Tip(code="dark_photo"))
    words = listing.title.split()
    if len(listing.title) < 15 or len(words) < 3:
        tips.append(Tip(code="short_title"))
    letters = [c for c in listing.title if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        tips.append(Tip(code="shouting_title"))
    if len(listing.description) < 80:
        tips.append(Tip(code="short_description"))
    price = await suggest_price(
        db,
        ai,
        title=listing.title,
        description=listing.description,
        category=listing.category_slug,
        exclude=listing.id,
    )
    if (
        price is not None
        and price.basis == "similar"
        and listing.price_cents > price.high_cents * 1.3
    ):
        tips.append(
            Tip(
                code="price_above_similar",
                data={"low_cents": price.low_cents, "high_cents": price.high_cents},
            )
        )
    return tips


# --- routes ---------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/assist", tags=["assistant"])


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def get_ai(request: Request) -> AI:
    ai: AI = request.app.state.ai
    return ai


AIDep = Annotated[AI, Depends(get_ai)]


class AssistStatus(BaseModel):
    draft: bool


class PriceIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=5000)
    category_slug: str | None = Field(default=None, max_length=40)


@router.get("/status")
async def status(ai: AIDep, db: DbSession) -> AssistStatus:
    """Which assistant features need a model and have one (the app hides the others)."""
    return AssistStatus(draft=await ai.available(db))


@router.post(
    "/draft",
    dependencies=[Depends(rate_limit("assist_draft", limit=30, window_seconds=3600))],
)
async def draft(
    user: CurrentUser,
    ai: AIDep,
    db: DbSession,
    files: Annotated[list[UploadFile], File()],
) -> Draft:
    """A listing draft from up to three photos. Nothing is saved: the form shows it for review."""
    settings = get_settings()
    if not files:
        raise AppError("Add at least one photo.", code="photo_required")
    photos: list[bytes] = []
    for file in files[:MAX_PHOTOS]:
        data = await file.read(settings.max_photo_bytes + 1)
        photos.append(
            await asyncio.to_thread(
                model_input,
                data,
                max_bytes=settings.max_photo_bytes,
                max_pixels=settings.max_image_pixels,
            )
        )
    return await draft_from_photos(db, ai, photos)


@router.post(
    "/price",
    dependencies=[Depends(rate_limit("assist_price", limit=120, window_seconds=3600))],
)
async def price(
    body: PriceIn, user: CurrentUser, ai: AIDep, db: DbSession
) -> PriceSuggestion | None:
    return await suggest_price(
        db, ai, title=body.title, description=body.description, category=body.category_slug
    )


@router.get("/listings/{listing_id}/tips")
async def tips(
    listing_id: uuid.UUID,
    user: CurrentUser,
    ai: AIDep,
    db: DbSession,
    storage: Annotated[Storage, Depends(get_storage)],
) -> list[Tip]:
    listing = await get_owned_listing(db, listing_id, user)
    return await listing_tips(db, ai, storage, listing)
