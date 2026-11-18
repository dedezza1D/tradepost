import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from tradepost.catalog.models import ItemCondition, ListingStatus
from tradepost.identity.schemas import BR_STATES

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=120)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=10, max_length=5000)
]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
PriceCents = Annotated[int, Field(gt=0, le=100_000_000)]
Stock = Annotated[int, Field(ge=0, le=10_000)]
ShippingCents = Annotated[int, Field(ge=0, le=1_000_000)]


def _state(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.upper()
    if value not in BR_STATES:
        raise ValueError("unknown Brazilian state code")
    return value


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str


class ListingCreate(BaseModel):
    title: Title
    description: Description
    category_slug: str = Field(max_length=40)
    condition: ItemCondition
    price_cents: PriceCents
    stock: Stock
    shipping_cents: ShippingCents = 0
    city: City
    state: str

    _state = field_validator("state")(_state)


class ListingUpdate(BaseModel):
    """Partial update. ``version`` must be the version the client last read."""

    version: int
    title: Title | None = None
    description: Description | None = None
    category_slug: str | None = Field(default=None, max_length=40)
    condition: ItemCondition | None = None
    price_cents: PriceCents | None = None
    stock: Stock | None = None
    shipping_cents: ShippingCents | None = None
    city: City | None = None
    state: str | None = None

    _state = field_validator("state")(_state)


class PhotoOut(BaseModel):
    id: uuid.UUID
    position: int
    url: str
    thumb_url: str
    width: int
    height: int


class SellerOut(BaseModel):
    id: uuid.UUID
    display_name: str
    rating_avg: float | None = None
    rating_count: int = 0


class ListingOut(BaseModel):
    id: uuid.UUID
    seller: SellerOut
    title: str
    description: str
    category_slug: str
    condition: ItemCondition
    price_cents: int
    currency: str
    shipping_cents: int
    stock: int
    city: str
    state: str
    municipality_id: int | None
    status: ListingStatus
    version: int
    photos: list[PhotoOut]
    created_at: datetime
    published_at: datetime | None


class ListingCard(BaseModel):
    id: uuid.UUID
    title: str
    price_cents: int
    currency: str
    condition: ItemCondition
    city: str
    state: str
    status: ListingStatus
    thumb_url: str | None
    published_at: datetime | None
    distance_km: float | None = None


class ListingPage(BaseModel):
    items: list[ListingCard]
    next_cursor: str | None


SortOrder = Literal["relevance", "newest", "price_asc", "price_desc", "nearest"]


class SearchParams(BaseModel):
    q: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=40)
    condition: ItemCondition | None = None
    state: str | None = None
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    # City (IBGE code) to measure distance from, and the search radius.
    near: int | None = Field(default=None, ge=1_000_000, le=9_999_999)
    radius_km: int = Field(default=50, ge=1, le=500)
    sort: SortOrder = "relevance"
    cursor: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=24, ge=1, le=60)

    _state = field_validator("state")(_state)
