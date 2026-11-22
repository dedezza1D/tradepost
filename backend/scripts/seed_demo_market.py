"""Load a lively demo marketplace: many sellers, buyers and weeks of activity around the catalog.

Usage (with the target database and storage settings in the environment):

    uv run python -m eval.listing_photos                # fetch the photos into eval/.cache
    uv run python -m scripts.seed_demo_market           # load; adds only what is missing
    uv run python -m scripts.seed_demo_market --reset   # remove the load, then load it again
    uv run python -m scripts.seed_demo_market --remove  # remove the load

The photos, titles and credits come from scripts/demo_catalog.json (see seed_demo_catalog), now
spread over dozens of sellers; names and texts come from scripts/demo_market.json. Everything
is fictional. Accounts use the reserved ``demo.tradepost.invalid`` domain, which the sign-up
form refuses, and a password hash that matches no password: they cannot sign in.

The load is a simulation: a plan of dated events (follows, saves, questions, chats, offers,
orders, reviews) is built from a fixed seed and replayed in chronological order through the
domain services, so counters, stock, ledger and notifications come out as the app itself would
leave them. Dates are then moved to when each event "happened", relative to the moment of the
load. Reputation looks at the last 90 days and trending at the last 14, so the picture fades
with time: ``--reset`` brings it back to today.

The listings of the single-seller catalog are paused while this load is in place (the same
photos would show twice) and resumed by ``--remove``. Removing deletes only what belongs to the
demo accounts. A demo seller with an order from any other account is kept, with the listings of
those orders marked as removed, so that order history stays intact.
"""

import argparse
import asyncio
import hashlib
import io
import json
import random
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from sqlalchemy import event, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from eval.listing_photos import PHOTOS
from scripts.seed_demo_catalog import CATALOG
from scripts.seed_demo_catalog import SELLER_EMAIL as CATALOG_SELLER_EMAIL
from scripts.seed_demo_catalog import description as credited
from tradepost import questions
from tradepost.catalog import service as catalog
from tradepost.catalog.models import ItemCondition, Listing, ListingStatus
from tradepost.catalog.schemas import ListingCreate, ListingUpdate, VariantIn
from tradepost.chat import service as chat
from tradepost.chat.models import Conversation, Offer
from tradepost.config import Settings, get_settings
from tradepost.db import Base, Database
from tradepost.geo.service import resolve_city
from tradepost.identity.models import User
from tradepost.media.images import process_image
from tradepost.media.storage import Storage, build_storage
from tradepost.orders import service as orders
from tradepost.orders.models import Checkout, CheckoutStatus, Order
from tradepost.payments.base import CardData, PaymentRegistry
from tradepost.payments.fake import APPROVED_TEST_TOKEN, FakeProvider
from tradepost.reviews import service as reviews
from tradepost.social import service as social
from tradepost.social.models import Follow

DATA = Path(__file__).with_name("demo_market.json")
DOMAIN = "demo.tradepost.invalid"
SEED = 20261122
MARKER = "Anúncio de demonstração do tradepost: não está à venda."
PROVIDER = "fake"
LOCK = "tradepost-demo-market"

DAY = 24.0
# Individual sellers repeat this pattern of reputations; shops name theirs in the data file.
TIER_CYCLE = (
    "good", "new", "new", "top", "new", "good", "new", "attention", "new", "good", "new", "new",
)  # fmt: skip
SELLER_REACH = {"top": 1.0, "good": 0.55, "attention": 0.3, "new": 0.18}
RATINGS = {
    "top": ((5, 86), (4, 14)),
    "good": ((5, 55), (4, 33), (3, 12)),
    "attention": ((1, 20), (2, 25), (3, 30), (4, 15), (5, 10)),
    "new": ((5, 60), (4, 30), (3, 10)),
}
# Shops keep a few units of these kinds; everything else is a single used item.
MULTI_STOCK = ("fone", "air_fryer", "liquidificador")
SHOE_SIZES = ("37", "38", "39", "40", "41", "42")
CONVERSATIONS = (
    ("chat", 38),
    ("declined", 14),
    ("pending", 10),
    ("countered_pending", 8),
    ("countered_declined", 8),
    ("cancelled", 7),
    ("expired", 6),
    ("accepted_unpaid", 6),
    ("off_platform", 3),
)
OFFER_HOURS = 24.0


# --- the plan --------------------------------------------------------------------------------
#
# Times in the plan are ages: hours before the moment of the load. A larger age is further in
# the past.


@dataclass(frozen=True)
class Size:
    shops: int = 10
    individuals: int = 28
    buyers: int = 110


@dataclass(frozen=True)
class Person:
    email: str
    name: str
    bio: str | None
    city: str
    state: str
    avatar: bool
    joined: float
    tier: str | None = None  # sellers only: the reputation their history adds up to
    shop: bool = False


@dataclass(frozen=True)
class Item:
    """A listing with a photo, on sale when the load ends."""

    seller: str
    title: str
    description: str
    category: str
    condition: str
    price_cents: int
    shipping_cents: int
    stock: int
    variants: tuple[tuple[str, int], ...]
    photo: str
    published: float


@dataclass(frozen=True)
class Event:
    age: float
    seq: int
    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class Plan:
    people: tuple[Person, ...]
    items: tuple[Item, ...]
    events: tuple[Event, ...]

    @property
    def sellers(self) -> tuple[Person, ...]:
        return tuple(p for p in self.people if p.tier is not None)


def kind_of(entry: dict[str, Any]) -> str:
    return str(entry["photo"]).rsplit("-", 1)[0]


def round_price(cents: float) -> int:
    step = 1000 if cents >= 20000 else 100
    return max(step, int(cents // step) * step)


class _Planner:
    def __init__(self, size: Size, seed: int) -> None:
        self.rng = random.Random(seed)  # noqa: S311 - a reproducible plan, not security
        self.size = size
        self.data: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
        self.catalog: list[dict[str, Any]] = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.people: list[Person] = []
        self.activity: dict[str, float] = {}
        self.reach: dict[str, float] = {}
        self.visible: list[dict[str, Any]] = []
        self.events: list[Event] = []
        self.drops: dict[tuple[str, str], tuple[float, int]] = {}
        self.pairs: list[tuple[str, str, str]] = []
        self.sold_pools: dict[str, list[list[Any]]] = {}
        self.order_count = 0
        self.offer_count = 0
        self.question_count = 0

    def add(self, age: float, kind: str, **data: Any) -> None:
        self.events.append(Event(age, len(self.events), kind, data))

    def build(self) -> Plan:
        self._people()
        self._listings()
        self._follows()
        self._saves()
        self._questions()
        self._price_drops()
        self._orders()
        self._conversations()
        items = tuple(
            Item(
                seller=d["seller"],
                title=d["title"],
                description=d["description"],
                category=d["category"],
                condition=d["condition"],
                price_cents=d["price_cents"],
                shipping_cents=d["shipping_cents"],
                stock=d["stock"] + sum(d["sold"].values()),
                variants=tuple((n, s + d["sold"].get(n, 0)) for n, s in d["variants"]),
                photo=d["photo"],
                published=d["published"],
            )
            for d in self.visible
        )
        ordered = sorted(self.events, key=lambda e: (-e.age, e.seq))
        return Plan(tuple(self.people), items, tuple(ordered))

    # --- people ------------------------------------------------------------------------------

    def _people(self) -> None:
        rng, data = self.rng, self.data
        names = [
            f"{first} {initial}." for first in data["first_names"] for initial in data["initials"]
        ]
        rng.shuffle(names)
        cities, weights = data["cities"], [c[2] for c in data["cities"]]

        def somewhere() -> tuple[str, str]:
            city = rng.choices(cities, weights=weights)[0]
            return city[0], city[1]

        shops = data["shops"][: self.size.shops]
        homes = self._seller_cities({(s["city"][0], s["city"][1]) for s in shops})
        for index, shop in enumerate(shops):
            self.people.append(
                Person(
                    email=f"loja{index + 1:02d}@{DOMAIN}",
                    name=shop["name"],
                    bio=shop["bio"],
                    city=shop["city"][0],
                    state=shop["city"][1],
                    avatar=True,
                    joined=DAY * rng.uniform(150, 420),
                    tier=shop["tier"],
                    shop=True,
                )
            )
        for index in range(self.size.individuals):
            tier = TIER_CYCLE[index % len(TIER_CYCLE)]
            city, state = homes[index] if index < len(homes) else somewhere()
            self.people.append(
                Person(
                    email=f"vende{index + 1:03d}@{DOMAIN}",
                    name=names.pop(),
                    bio=rng.choice(data["seller_bios"]),
                    city=city,
                    state=state,
                    avatar=rng.random() < 0.55,
                    joined=DAY * (rng.uniform(4, 200) if tier == "new" else rng.uniform(95, 380)),
                    tier=tier,
                )
            )
        for index in range(self.size.buyers):
            city, state = somewhere()
            self.people.append(
                Person(
                    email=f"compra{index + 1:03d}@{DOMAIN}",
                    name=names.pop(),
                    bio=rng.choice(data["buyer_bios"]) if rng.random() < 0.45 else None,
                    city=city,
                    state=state,
                    avatar=rng.random() < 0.35,
                    joined=DAY * rng.uniform(1.5, 300),
                )
            )
        for person in self.people:
            # How much someone browses, follows and buys: a few people do most of it.
            if person.tier is None:
                self.activity[person.email] = rng.lognormvariate(0, 0.9)
            else:
                self.activity[person.email] = 0.45 * rng.lognormvariate(0, 0.6)
                self.reach[person.email] = (
                    SELLER_REACH[person.tier]
                    * (1.7 if person.shop else 1.0)
                    * rng.lognormvariate(0, 0.35)
                )

    def _seller_cities(self, taken: set[tuple[str, str]]) -> list[tuple[str, str]]:
        """A different city for each individual seller, every state first, then the larger ones.

        States without a shop get their largest listed city; the remaining sellers go to other
        cities drawn by weight without repeats. The order is shuffled so that reputation tiers,
        which follow the seller's position, do not follow the region.
        """
        rng = self.rng
        pool = [c for c in self.data["cities"] if (c[0], c[1]) not in taken]
        states = {state for _, state in taken}
        homes: list[tuple[str, str]] = []
        rest = []
        for name, state, weight in sorted(pool, key=lambda c: -c[2]):
            if state in states:
                rest.append((name, state, weight))
            else:
                states.add(state)
                homes.append((name, state))
        while rest and len(homes) < self.size.individuals:
            name, state, _ = rest.pop(rng.choices(range(len(rest)), [c[2] for c in rest])[0])
            homes.append((name, state))
        homes = homes[: self.size.individuals]
        rng.shuffle(homes)
        return homes

    def _by_email(self, email: str) -> Person:
        return next(p for p in self.people if p.email == email)

    def _someone(self, age: float, *exclude: str) -> Person | None:
        """An account that already existed at ``age``, the busier ones more often."""
        pool = [p for p in self.people if p.joined > age + 1 and p.email not in exclude]
        if not pool:
            return None
        return self.rng.choices(pool, weights=[self.activity[p.email] for p in pool])[0]

    def _sample(self, pool: list[Person], count: int) -> list[Person]:
        """Weighted sample without repetition (Efraimidis-Spirakis keys)."""
        keyed = [(self.rng.random() ** (1 / self.activity[p.email]), p) for p in pool]
        keyed.sort(key=lambda pair: pair[0], reverse=True)
        return [p for _, p in keyed[:count]]

    # --- listings on sale --------------------------------------------------------------------

    def _listings(self) -> None:
        rng = self.rng
        by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in self.catalog:
            by_kind[kind_of(entry)].append(entry)
        shops = [p for p in self.people if p.shop]
        for person, shop in zip(shops, self.data["shops"], strict=False):
            taken = 0
            while taken < shop["listings"] and any(by_kind[k] for k in shop["kinds"]):
                for kind in shop["kinds"]:
                    if taken < shop["listings"] and by_kind[kind]:
                        self._list(person, by_kind[kind].pop(0))
                        taken += 1
        rest = [entry for entries in by_kind.values() for entry in entries]
        rng.shuffle(rest)
        individuals = [p for p in self.people if p.tier is not None and not p.shop]
        for _ in range(3):
            for person in individuals:
                if rest:
                    self._list(person, rest.pop())

    def _list(self, seller: Person, entry: dict[str, Any]) -> None:
        rng = self.rng
        kind = kind_of(entry)
        info = self.data["kinds"][kind]
        variants: list[tuple[str, int]] = []
        stock = 1
        if seller.shop and kind == "tenis":
            variants = [(size, rng.randint(1, 2)) for size in sorted(rng.sample(SHOE_SIZES, 3))]
            stock = sum(units for _, units in variants)
        elif seller.shop and kind in MULTI_STOCK:
            stock = rng.randint(2, 4)
        text_ = f"{entry['description']} {rng.choice(info['extras'])}"
        self.visible.append(
            {
                "seller": seller.email,
                "title": str(entry["title"]),
                "description": credited({**entry, "description": text_}),
                "category": str(entry["category"]),
                "condition": str(entry["condition"]),
                "price_cents": int(entry["price_cents"]),
                "shipping_cents": int(info["shipping_cents"]),
                "stock": stock,
                "variants": variants,
                "sold": {},
                "photo": str(entry["photo"]),
                # About half the listings are from the last two weeks, a few a month old.
                "published": min(DAY * (0.15 + 40 * rng.random() ** 1.6), seller.joined - 2),
                "kind": kind,
                "reach": self.reach[seller.email] ** 0.5 * rng.lognormvariate(0, 0.7),
            }
        )

    def _price_at(self, item: dict[str, Any], age: float) -> int:
        drop = self.drops.get((item["seller"], item["title"]))
        return drop[1] if drop is not None and age < drop[0] else int(item["price_cents"])

    # --- follows, saves, questions -----------------------------------------------------------

    def _follows(self) -> None:
        rng = self.rng
        sellers = sorted(
            (p for p in self.people if p.tier is not None), key=lambda p: -self.reach[p.email]
        )
        most = min(90, int(0.58 * len(self.people)))
        for rank, seller in enumerate(sellers):
            pool = [p for p in self.people if p.email != seller.email]
            for follower in self._sample(pool, round(most / (rank + 1) ** 0.75)):
                since = min(follower.joined, seller.joined) - 0.5
                self.add(
                    max(0.3, since * rng.random() ** 1.8),
                    "follow",
                    follower=follower.email,
                    followee=seller.email,
                )

    def _saves(self) -> None:
        rng = self.rng
        most = min(30, int(0.22 * len(self.people)))
        ranked = sorted(self.visible, key=lambda d: -d["reach"])
        for rank, item in enumerate(ranked):
            pool = [p for p in self.people if p.email != item["seller"]]
            # A listing needs a few days on sale to gather its saves.
            share = min(1.0, (item["published"] / (3 * DAY)) ** 0.5)
            for saver in self._sample(pool, round(share * most / (rank + 1) ** 0.5)):
                since = min(saver.joined, item["published"]) - 0.1
                self.add(
                    max(0.05, since * rng.random() ** 1.15),
                    "save",
                    user=saver.email,
                    seller=item["seller"],
                    title=item["title"],
                )

    def _questions(self) -> None:
        rng = self.rng
        popular = {id(d) for d in sorted(self.visible, key=lambda d: -d["reach"])[:10]}
        for item in self.visible:
            count = rng.choices((0, 1, 2, 3, 4), weights=(25, 35, 22, 12, 6))[0]
            count += 1 if id(item) in popular else 0
            pairs = self.data["kinds"][item["kind"]]["questions"] + self.data["questions"]
            for body, answer in rng.sample(pairs, count):
                age = item["published"] * rng.random() ** 1.3
                asker = self._someone(age, item["seller"])
                if asker is None or age < 0.2:
                    continue
                self.question_count += 1
                self.add(
                    age,
                    "ask",
                    key=self.question_count,
                    user=asker.email,
                    seller=item["seller"],
                    title=item["title"],
                    body=body,
                )
                answered = age - rng.uniform(0.2, 30)
                if answered > 0.1 and rng.random() < 0.9:
                    self.add(
                        answered,
                        "answer",
                        key=self.question_count,
                        seller=item["seller"],
                        body=answer,
                    )

    def _price_drops(self) -> None:
        rng = self.rng
        pool = [d for d in self.visible if not d["variants"] and d["published"] > 5 * DAY]
        for item in rng.sample(pool, min(len(pool), max(1, round(0.1 * len(self.visible))))):
            lower = round_price(item["price_cents"] * rng.uniform(0.85, 0.93))
            if lower >= item["price_cents"]:
                continue
            age = item["published"] * rng.uniform(0.25, 0.6)
            self.drops[(item["seller"], item["title"])] = (age, lower)
            self.add(age, "price_drop", seller=item["seller"], title=item["title"], price=lower)

    # --- orders ------------------------------------------------------------------------------

    def _orders(self) -> None:
        rng = self.rng
        for seller in (p for p in self.people if p.tier is not None):
            tier = seller.tier or "new"
            done = {
                "top": rng.randint(11, 16),
                "good": rng.randint(4, 7),
                "attention": rng.randint(4, 6),
                "new": rng.choice((0, 0, 1, 1, 2)),
            }[tier]
            recent = {"top": rng.randint(2, 4), "good": rng.randint(0, 2), "attention": 1}.get(
                tier, 0
            )
            oldest = min(78.0, seller.joined / DAY - 3)
            # Completed sales are old enough to have been shipped and delivered by now.
            floor = 16.0 if tier == "attention" else 11.0
            wanted: list[tuple[float, str, bool]] = []
            for number in range(done):
                if oldest > floor:
                    late = tier == "attention" and number < 2
                    wanted.append((DAY * rng.uniform(floor, oldest), "done", late))
            for _ in range(recent):
                wanted.append((DAY * rng.uniform(0.15, min(9.0, oldest)), "recent", False))
            if tier == "attention":
                wanted.append((DAY * rng.uniform(12, min(50.0, oldest)), "refund", False))
            if oldest > 2 and rng.random() < 0.12:
                unpaid = rng.choice(("cancel", "expire"))
                wanted.append((DAY * rng.uniform(1, min(40.0, oldest)), unpaid, False))
            slots: list[tuple[float, int, str]] = []
            for age, outcome, late in wanted:
                self._order(seller, age, outcome, late, slots)
            self._reviews(seller, slots)

    def _sold_item(self, seller: Person) -> list[Any] | None:
        """The next thing this seller sold in the past: from their line of goods first."""
        pool = self.sold_pools.get(seller.email)
        if pool is None:
            groups = list(self.data["sold"])
            shop = next((s for s in self.data["shops"] if s["name"] == seller.name), None)
            mine = shop["sold"] if shop and seller.shop else self.rng.sample(groups, 2)
            pool = [item for g in groups if g not in mine for item in self.data["sold"][g]]
            self.rng.shuffle(pool)
            # Taken from the end: the seller's main line, then the second, then anything.
            for group in reversed(mine):
                line = list(self.data["sold"][group])
                self.rng.shuffle(line)
                pool += line
            self.sold_pools[seller.email] = pool
        return pool.pop() if pool else None

    def _order(
        self,
        seller: Person,
        age: float,
        outcome: str,
        late: bool,
        slots: list[tuple[float, int, str]],
    ) -> None:
        rng = self.rng
        buyer = self._someone(age + 0.5, seller.email)
        if buyer is None:
            return
        lines: list[tuple[str, int, str | None]] = []
        offer: int | None = None
        in_stock = [
            d
            for d in self.visible
            if d["seller"] == seller.email and d["stock"] > 1 and d["published"] > age + 1
        ]
        if outcome == "recent" and in_stock:
            # A unit of something still on sale: this is what moves the trending tab.
            item = rng.choice(in_stock)
            variant = rng.choice(item["variants"])[0] if item["variants"] else None
            item["sold"][variant] = item["sold"].get(variant, 0) + 1
            lines.append((item["title"], 1, variant))
        else:
            count = 2 if outcome == "done" and rng.random() < 0.12 else 1
            for _ in range(count):
                sold = self._sold_item(seller)
                published = min(age + DAY * rng.uniform(1, 12), seller.joined - 1)
                if sold is None or published < age + 1:
                    continue
                title, category, price = sold
                notes = rng.sample(self.data["sold_notes"], 2)
                large = category in ("furniture", "appliances")
                self.add(
                    published,
                    "list",
                    seller=seller.email,
                    title=title,
                    description=f"{notes[0]} {notes[1]}\n\n{MARKER}",
                    category=category,
                    condition="new" if rng.random() < 0.15 else "used",
                    price_cents=price,
                    shipping_cents=rng.choice((0, 8000, 12000) if large else (0, 1500, 2500, 3500)),
                )
                lines.append((title, 1, None))
                if outcome in ("cancel", "expire"):
                    # Not bought after all: the seller takes the listing down a little later.
                    gone = max(0.05, age - rng.uniform(1, 30))
                    self.add(gone, "unlist", seller=seller.email, title=title)
                if outcome == "done" and count == 1 and rng.random() < 0.2:
                    offer = self._negotiated(seller, buyer, title, price, published, age)
        if not lines:
            return
        self.order_count += 1
        key = self.order_count
        method = "pix" if rng.random() < 0.6 else "card"
        pay_after = rng.uniform(1.5, 20)
        unpaid_after = rng.uniform(4, 12) if outcome == "cancel" else rng.uniform(15.5, 17)
        installments = rng.choice((1, 1, 1, 2, 3, 6)) if method == "card" else None
        declined_first = rng.random() < 0.06
        self.add(
            age, "order", key=key, buyer=buyer.email, seller=seller.email, lines=lines, offer=offer
        )
        if outcome in ("cancel", "expire"):
            self.add(age - unpaid_after / 60, "abandon", key=key, expired=outcome == "expire")
            return
        paid = age - pay_after / 60
        self.add(
            paid,
            "pay",
            key=key,
            buyer=buyer.email,
            method=method,
            installments=installments,
            declined_first=declined_first,
        )
        if outcome == "refund":
            refunded = paid - rng.uniform(20, 60)
            if refunded > 0.1:
                self.add(refunded, "refund", key=key, seller=seller.email)
            return
        shipped = paid - (rng.uniform(85, 170) if late else rng.uniform(5, 58))
        if shipped <= 0.1:
            return
        self.add(shipped, "ship", key=key, seller=seller.email, tracking=f"DEMO{key:06d}BR")
        delivered = shipped - rng.uniform(30, 150)
        if delivered <= 0.1:
            return
        self.add(delivered, "deliver", key=key, buyer=buyer.email)
        reviewed = delivered - rng.uniform(1, 70)
        if reviewed > 0.1 and rng.random() < 0.8:
            slots.append((reviewed, key, buyer.email))

    def _reviews(self, seller: Person, slots: list[tuple[float, int, str]]) -> None:
        rng = self.rng
        tier = seller.tier or "new"
        if not slots:
            return
        stars, weights = zip(*RATINGS[tier], strict=True)
        ratings = rng.choices(stars, weights=weights, k=len(slots))
        # The ratings must add up to the reputation the seller's history is meant to show.
        floor = {"top": 4.5, "good": 4.0}.get(tier)
        while floor is not None and sum(ratings) / len(ratings) < floor:
            ratings[ratings.index(min(ratings))] = 5
        while tier == "attention" and sum(ratings) / len(ratings) >= 4.0:
            ratings[ratings.index(max(ratings))] = 2
        comments = {star: list(texts) for star, texts in self.data["reviews"].items()}
        for texts in comments.values():
            rng.shuffle(texts)
        for (age, key, buyer), rating in zip(slots, ratings, strict=True):
            pool = comments[str(rating)]
            comment = pool.pop() if pool and rng.random() > 0.28 else None
            self.add(age, "review", key=key, buyer=buyer, rating=rating, comment=comment)

    # --- conversations and offers ------------------------------------------------------------

    def _ages(self, last: float, steps: int, limit: float, gap: tuple[float, float]) -> list[float]:
        """Ages for ``steps`` moments in a row, the last one at ``last``, none before ``limit``."""
        gaps = [self.rng.uniform(*gap) for _ in range(steps - 1)]
        room = limit - last
        if sum(gaps) >= room:
            gaps = [g * 0.9 * room / sum(gaps) for g in gaps]
        return [last + sum(gaps[index:]) for index in range(steps)]

    def _offer_key(self) -> int:
        self.offer_count += 1
        return self.offer_count

    def _say(self, age: float, conv: tuple[str, str, str], who: str, body: str) -> None:
        self.add(age, "chat", conv=conv, who=who, body=body)

    def _negotiated(
        self, seller: Person, buyer: Person, title: str, price: int, published: float, age: float
    ) -> int | None:
        """The chat and the accepted offer behind an order. Returns the offer to pay."""
        rng, texts = self.rng, self.data["chats"]
        conv = (buyer.email, seller.email, title)
        last = age + rng.uniform(0.1, 3)
        limit = min(published, buyer.joined) - 0.2
        if last >= limit or conv in self.pairs:
            return None
        self.pairs.append(conv)
        counter = rng.random() < 0.4
        ages = self._ages(last, 8 if counter else 7, limit, (0.03, 2.5))
        opener = rng.choice(texts["openers"])
        amount = round_price(price * rng.uniform(0.82, 0.95))
        first = self._offer_key()
        self._say(ages[0], conv, "buyer", opener[0])
        self._say(ages[1], conv, "seller", opener[1])
        self._say(ages[2], conv, "buyer", rng.choice(texts["offer_intros"]))
        self.add(ages[3], "offer", conv=conv, key=first, amount=amount)
        if not counter:
            self.add(ages[4], "offer_accept", key=first, by=seller.email)
            self._say(ages[5], conv, "seller", rng.choice(texts["accepted"]))
            self._say(ages[6], conv, "buyer", rng.choice(texts["paying"]))
            return first
        second = self._offer_key()
        middle = max(amount, round_price((amount + price) / 2))
        self.add(ages[4], "offer_counter", key=first, new=second, by=seller.email, amount=middle)
        self._say(ages[5], conv, "seller", rng.choice(texts["countered"]))
        self.add(ages[6], "offer_accept", key=second, by=buyer.email)
        self._say(ages[7], conv, "buyer", rng.choice(texts["counter_accepted"]))
        return second

    def _conversations(self) -> None:
        rng, texts = self.rng, self.data["chats"]
        pool = [d for d in self.visible if not d["variants"] and d["published"] > 3]
        names, weights = zip(*CONVERSATIONS, strict=True)
        for _ in range(round(0.85 * len(self.visible)) if pool else 0):
            item = rng.choices(pool, weights=[d["reach"] for d in pool])[0]
            scenario = rng.choices(names, weights=weights)[0]
            seller = self._by_email(item["seller"])
            if scenario in ("pending", "countered_pending", "accepted_unpaid"):
                last = rng.uniform(0.4, 17)  # still inside the 24 hours an offer stays open
            elif scenario == "expired":
                last = rng.uniform(0.5, 20 * DAY) + OFFER_HOURS
            else:
                last = rng.uniform(19, 25 * DAY)
            limit = item["published"] - 0.2
            buyer = self._someone(limit, seller.email)
            if buyer is None or last + 0.5 >= limit:
                continue
            conv = (buyer.email, seller.email, item["title"])
            if conv in self.pairs:
                continue
            self.pairs.append(conv)
            limit = min(limit, buyer.joined - 0.2)
            opener = rng.choice(texts["openers"])
            said: list[tuple[str, str] | tuple[str, str, Any]] = [
                ("buyer", opener[0]),
                ("seller", opener[1]),
            ]
            if scenario == "chat":
                for ask, reply in rng.sample(texts["followups"], rng.randint(1, 3)):
                    said += [("buyer", ask), ("seller", reply)]
            elif scenario == "off_platform":
                ask, reply = rng.choice(texts["off_platform"])
                said += [("buyer", ask), ("seller", reply)]
            else:
                said.append(("buyer", rng.choice(texts["offer_intros"])))
                said.append(("offer", "buyer", None))
                if scenario == "declined":
                    said += [
                        ("offer_decline", "seller", None),
                        ("seller", rng.choice(texts["declined"])),
                    ]
                elif scenario == "accepted_unpaid":
                    said += [
                        ("offer_accept", "seller", None),
                        ("seller", rng.choice(texts["accepted"])),
                    ]
                elif scenario == "cancelled":
                    said += [
                        ("buyer", rng.choice(texts["cancelled"])),
                        ("offer_cancel", "buyer", None),
                    ]
                elif scenario.startswith("countered"):
                    said += [
                        ("offer_counter", "seller", None),
                        ("seller", rng.choice(texts["countered"])),
                    ]
                    if scenario == "countered_declined":
                        said.append(("offer_decline", "buyer", None))
                        said.append(("buyer", rng.choice(texts["counter_declined"])))
            ages = self._ages(last, len(said), limit, (0.03, 3))
            current: int | None = None
            amount = 0
            for age, step in zip(ages, said, strict=True):
                if len(step) == 2:
                    self._say(age, conv, step[0], step[1])
                    continue
                price = self._price_at(item, age)
                actor = buyer.email if step[1] == "buyer" else seller.email
                if step[0] == "offer":
                    current = self._offer_key()
                    amount = min(price, round_price(price * rng.uniform(0.78, 0.93)))
                    self.add(age, "offer", conv=conv, key=current, amount=amount)
                    if scenario == "expired":
                        self.add(age - OFFER_HOURS, "offer_expire", key=current)
                elif step[0] == "offer_counter":
                    previous, current = current, self._offer_key()
                    amount = min(price, max(amount, round_price((amount + price) / 2)))
                    self.add(age, step[0], key=previous, new=current, by=actor, amount=amount)
                else:
                    self.add(age, step[0], key=current, by=actor)


def build_plan(size: Size | None = None, seed: int = SEED) -> Plan:
    """The whole load, decided up front: the same seed and size always give the same plan."""
    return _Planner(size or Size(), seed).build()


# --- loading ---------------------------------------------------------------------------------


def cached_photo(name: str) -> bytes:
    path = PHOTOS / name
    if not path.exists():
        raise SystemExit(f"{path} is missing: run `uv run python -m eval.listing_photos` first.")
    return path.read_bytes()


def avatar_image(email: str) -> bytes:
    """A symmetric pattern drawn from the address: no photo of anyone, real or generated."""
    digest = hashlib.sha256(email.encode()).digest()
    hue = digest[0] / 255
    tone = Image.new("HSV", (1, 1), (int(hue * 255), 150, 190)).convert("RGB").getpixel((0, 0))
    image = Image.new("RGB", (240, 240), (244, 240, 236))
    draw = ImageDraw.Draw(image)
    for row in range(5):
        for column in range(3):
            if digest[1 + row * 3 + column] % 2:
                for x in {column, 4 - column}:
                    draw.rectangle(
                        (20 + x * 40, 20 + row * 40, 59 + x * 40, 59 + row * 40), fill=tone
                    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class _Clock:
    now: datetime | None = None


@contextmanager
def _simulated_time() -> Iterator[_Clock]:
    """While the clock is set, rows created through the ORM are dated by it instead of by the
    database: a message, an order or a notification gets the moment of the event being
    replayed. Columns the services set themselves are moved by the loader afterwards."""
    clock = _Clock()

    def stamp(target: Any, args: Any, kwargs: dict[str, Any]) -> None:
        if clock.now is not None and hasattr(type(target), "created_at"):
            kwargs.setdefault("created_at", clock.now)

    event.listen(Base, "init", stamp, propagate=True)
    try:
        yield clock
    finally:
        event.remove(Base, "init", stamp)


def _demo_users() -> Any:
    return select(User).where(User.email.like(f"%@{DOMAIN}"))


async def _lock(db: AsyncSession) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": LOCK})


async def _ensure_user(
    db: AsyncSession, storage: Storage, settings: Settings, person: Person, anchor: datetime
) -> tuple[User, list[str]]:
    stored: list[str] = []
    user = await db.scalar(select(User).where(User.email == person.email))
    if user is None:
        town = await resolve_city(db, person.city, person.state)
        joined = anchor - timedelta(hours=person.joined)
        user = User(
            email=person.email,
            password_hash="!",  # noqa: S106 - not a valid hash: the account cannot sign in
            display_name=person.name,
            bio=person.bio,
            city=town.name,
            state=town.state,
            municipality_id=town.ibge_code,
            created_at=joined,
            password_changed_at=joined,
        )
        db.add(user)
        await db.flush()
    if person.avatar and user.avatar_key is None:
        # As the avatar upload does: one small rendition, counted against the media quota.
        processed = await asyncio.to_thread(
            process_image,
            avatar_image(person.email),
            max_bytes=settings.max_photo_bytes,
            max_pixels=settings.max_image_pixels,
        )
        size = len(processed.thumb)
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:id)"), {"id": catalog.MEDIA_QUOTA_LOCK}
        )
        if await catalog.media_used(db) + size > settings.media_quota_bytes:
            raise catalog.StorageFull("Photo storage is full.")
        key = f"avatars/{user.id}/{uuid.uuid4()}.webp"
        await storage.put(key, processed.thumb, "image/webp")
        stored.append(key)
        user.avatar_key, user.avatar_bytes = key, size
        await db.flush()
    return user, stored


async def _publish(
    db: AsyncSession,
    storage: Storage,
    settings: Settings,
    seller: User,
    item: Item,
    anchor: datetime,
    photo: Callable[[str], bytes],
) -> list[str]:
    assert seller.city is not None
    assert seller.state is not None
    listing = await catalog.create_listing(
        db,
        seller,
        ListingCreate(
            title=item.title,
            description=item.description,
            category_slug=item.category,
            condition=ItemCondition(item.condition),
            price_cents=item.price_cents,
            stock=item.stock,
            shipping_cents=item.shipping_cents,
            city=seller.city,
            state=seller.state,
        ),
    )
    await db.refresh(listing, ["photos"])
    data = await asyncio.to_thread(photo, item.photo)
    stored = await catalog.add_photo(db, storage, settings, listing, data)
    keys = [stored.key_full, stored.key_thumb]
    try:
        if item.variants:
            await catalog.set_variants(
                db, listing, [VariantIn(name=name, stock=units) for name, units in item.variants]
            )
        published = anchor - timedelta(hours=item.published)
        listing.created_at = published - timedelta(minutes=12)
        listing.published_at = published
        await catalog.publish_listing(db, listing)
        if listing.status != ListingStatus.ACTIVE:
            raise RuntimeError(f"'{item.title}' was held for review: reword it in the data file.")
    except BaseException:
        for key in keys:
            await storage.delete(key)
        raise
    return keys


async def _accounts_and_listings(
    database: Database,
    storage: Storage,
    settings: Settings,
    plan: Plan,
    anchor: datetime,
    photo: Callable[[str], bytes],
) -> None:
    """Accounts with their profile and the listings on sale. One short transaction per account:
    each photo upload takes the media quota lock, which real uploads also wait on."""
    on_sale: dict[str, list[Item]] = defaultdict(list)
    for item in plan.items:
        on_sale[item.seller].append(item)
    for person in plan.people:
        stored: list[str] = []
        try:
            async with database.sessionmaker() as db, db.begin():
                user, stored = await _ensure_user(db, storage, settings, person, anchor)
                listed = set(
                    await db.scalars(select(Listing.title).where(Listing.seller_id == user.id))
                )
                for item in on_sale[person.email]:
                    if item.title not in listed:
                        stored += await _publish(db, storage, settings, user, item, anchor, photo)
        except BaseException:
            for key in stored:
                await storage.delete(key)
            raise


class _Replay:
    """Replays the plan's events, oldest first, through the domain services."""

    def __init__(
        self, db: AsyncSession, settings: Settings, anchor: datetime, clock: _Clock
    ) -> None:
        self.db = db
        self.settings = settings
        self.anchor = anchor
        self.clock = clock
        self.provider = FakeProvider()
        self.registry = PaymentRegistry([self.provider])
        self.users: dict[str, User] = {}
        self.listings: dict[tuple[str, str], uuid.UUID] = {}
        self.questions: dict[int, uuid.UUID] = {}
        self.conversations: dict[tuple[str, str, str], Conversation] = {}
        self.offers: dict[int, Offer] = {}
        self.open: dict[int, Checkout] = {}
        self.orders: dict[int, Order] = {}
        self.follows: list[tuple[uuid.UUID, uuid.UUID, datetime]] = []
        self.saves: list[tuple[uuid.UUID, uuid.UUID, datetime]] = []
        self.answered: dict[uuid.UUID, datetime | None] = {}
        self.answers: dict[uuid.UUID, datetime] = {}
        self.checkout_dates: dict[uuid.UUID, dict[str, datetime | None]] = {}
        self.order_dates: dict[uuid.UUID, dict[str, datetime]] = {}
        # Listings stay referenced so the session keeps them instead of reading them again on
        # every save, question and message; ``on_sale`` are those that existed before the replay.
        self.held: dict[uuid.UUID, Listing] = {}
        self.on_sale: frozenset[uuid.UUID] = frozenset()
        self.skipped = 0

    @property
    def now(self) -> datetime:
        assert self.clock.now is not None
        return self.clock.now

    async def prepare(self) -> None:
        self.users = {user.email: user for user in await self.db.scalars(_demo_users())}
        by_id = {user.id: email for email, user in self.users.items()}
        rows = await self.db.scalars(
            select(Listing).where(
                Listing.seller_id.in_(list(by_id)), Listing.status == ListingStatus.ACTIVE
            )
        )
        for listing in rows:
            self.listings[(by_id[listing.seller_id], listing.title)] = listing.id
            self.held[listing.id] = listing
        self.on_sale = frozenset(self.held)

    async def play(self, plan: Plan) -> None:
        for step in plan.events:
            self.clock.now = self.anchor - timedelta(hours=step.age)
            done = await getattr(self, f"_{step.kind}")(**step.data)
            self.skipped += done is False
            await self.db.flush()
        await self._settle_dates()

    async def _fresh(self, listing_id: uuid.UUID) -> Listing:
        # Stock and status change through plain SQL: read them again before relying on them.
        listing = await self.db.get(Listing, listing_id, populate_existing=True)
        assert listing is not None
        return listing

    async def _date_alerts(self) -> None:
        """Alerts to people who saved a listing are written by plain SQL, at the database's
        clock: move the ones of this transaction to the event being replayed."""
        await self.db.execute(
            text(
                """
                UPDATE notifications SET created_at = :at
                 WHERE created_at = transaction_timestamp()
                   AND kind IN ('saved_low_stock', 'saved_price_drop')
                   AND user_id = ANY(:users)
                """
            ),
            {"at": self.now, "users": [user.id for user in self.users.values()]},
        )

    # --- social ------------------------------------------------------------------------------

    async def _follow(self, follower: str, followee: str) -> None:
        who, target = self.users[follower], self.users[followee]
        await social.follow(self.db, who, target.id)
        self.follows.append((who.id, target.id, self.now))

    async def _save(self, user: str, seller: str, title: str) -> bool:
        listing_id = self.listings.get((seller, title))
        if listing_id is None:
            return False
        await social.save_listing(self.db, self.users[user], listing_id)
        self.saves.append((self.users[user].id, listing_id, self.now))
        return True

    async def _ask(self, key: int, user: str, seller: str, title: str, body: str) -> bool:
        listing_id = self.listings.get((seller, title))
        if listing_id is None:
            return False
        asked = await questions.ask(
            listing_id, questions.QuestionIn(body=body), self.users[user], self.db
        )
        self.questions[key] = asked.id
        return True

    async def _answer(self, key: int, seller: str, body: str) -> bool:
        question_id = self.questions.get(key)
        if question_id is None:
            return False
        await questions.answer(
            question_id, questions.AnswerIn(body=body), self.users[seller], self.db
        )
        self.answers[question_id] = self.now
        return True

    async def _price_drop(self, seller: str, title: str, price: int) -> bool:
        listing_id = self.listings.get((seller, title))
        if listing_id is None:
            return False
        listing = await self._fresh(listing_id)
        if price >= listing.price_cents:
            return False
        await catalog.update_listing(
            self.db, listing, ListingUpdate(version=listing.version, price_cents=price)
        )
        await self._date_alerts()
        return True

    async def _list(self, seller: str, title: str, **fields: Any) -> bool:
        """Something sold in the past. It never appears in the catalog again, so it carries no
        photo; it is put on sale directly because publishing requires one."""
        owner = self.users[seller]
        if (seller, title) in self.listings:
            return False
        assert owner.city is not None
        assert owner.state is not None
        condition = ItemCondition(fields.pop("condition"))
        category = fields.pop("category")
        listing = await catalog.create_listing(
            self.db,
            owner,
            ListingCreate(
                title=title,
                category_slug=category,
                condition=condition,
                stock=1,
                city=owner.city,
                state=owner.state,
                **fields,
            ),
        )
        listing.status = ListingStatus.ACTIVE
        listing.published_at = self.now
        self.listings[(seller, title)] = listing.id
        self.held[listing.id] = listing
        return True

    async def _unlist(self, seller: str, title: str) -> bool:
        listing_id = self.listings.pop((seller, title), None)
        if listing_id is None:
            return False
        await catalog.remove_listing(self.db, await self._fresh(listing_id))
        return True

    # --- chat and offers ---------------------------------------------------------------------

    async def _conversation(self, conv: tuple[str, str, str]) -> Conversation | None:
        found = self.conversations.get(conv)
        if found is None:
            listing_id = self.listings.get((conv[1], conv[2]))
            if listing_id is None:
                return None
            found = await chat.start(self.db, self.users[conv[0]], listing_id)
            self.conversations[conv] = found
        return found

    async def _chat(self, conv: tuple[str, str, str], who: str, body: str) -> bool:
        conversation = await self._conversation(conv)
        if conversation is None:
            return False
        sender = self.users[conv[0] if who == "buyer" else conv[1]]
        await chat.send(self.db, conversation, sender, body)
        return True

    async def _offer(self, conv: tuple[str, str, str], key: int, amount: int) -> bool:
        conversation = await self._conversation(conv)
        if conversation is None:
            return False
        listing = await self._fresh(conversation.listing_id)
        if listing.status != ListingStatus.ACTIVE or listing.stock < 1:
            return False
        amount = min(amount, listing.price_cents)
        self.offers[key] = await chat.make_offer(
            self.db, conversation, self.users[conv[0]], amount, 1
        )
        self.answered[self.offers[key].id] = None
        return True

    async def _offer_accept(self, key: int, by: str) -> bool:
        offer = self.offers.get(key)
        if offer is None:
            return False
        await chat.accept(self.db, offer.id, self.users[by])
        self.answered[offer.id] = self.now
        return True

    async def _offer_decline(self, key: int, by: str) -> bool:
        offer = self.offers.get(key)
        if offer is None:
            return False
        await chat.decline(self.db, offer.id, self.users[by])
        self.answered[offer.id] = self.now
        return True

    async def _offer_cancel(self, key: int, by: str) -> bool:
        offer = self.offers.get(key)
        if offer is None:
            return False
        await chat.cancel(self.db, offer.id, self.users[by])
        self.answered[offer.id] = self.now
        return True

    async def _offer_counter(self, key: int, new: int, by: str, amount: int) -> bool:
        offer = self.offers.get(key)
        if offer is None:
            return False
        conversation = await self.db.get(Conversation, offer.conversation_id)
        assert conversation is not None
        listing = await self._fresh(conversation.listing_id)
        if listing.status != ListingStatus.ACTIVE or listing.stock < 1:
            return False
        amount = min(amount, listing.price_cents)
        self.offers[new] = await chat.counter(self.db, offer.id, self.users[by], amount)
        self.answered[offer.id] = self.now
        self.answered[self.offers[new].id] = None
        return True

    async def _offer_expire(self, key: int) -> bool:
        offer = self.offers.get(key)
        if offer is None:
            return False
        # Whatever is really due is closed first, at the real time, as the job would do.
        moment, self.clock.now = self.now, None
        await chat.expire_due(self.db)
        self.clock.now = moment
        await self.db.execute(update(Offer).where(Offer.id == offer.id).values(expires_at=moment))
        await chat.expire_due(self.db)
        self.answered[offer.id] = moment
        return True

    # --- orders ------------------------------------------------------------------------------

    async def _order(
        self,
        key: int,
        buyer: str,
        seller: str,
        lines: list[tuple[str, int, str | None]],
        offer: int | None,
    ) -> bool:
        who = self.users[buyer]
        agreed: orders.AgreedPrice | None = None
        wanted: list[orders.Line] = []
        if offer is not None:
            accepted = self.offers.get(offer)
            if accepted is None:
                return False
            terms = await chat.checkout_terms(self.db, accepted.id, who)
            assert isinstance(terms, chat.OfferTerms)
            agreed = orders.AgreedPrice(
                offer_id=terms.offer_id,
                listing_id=terms.listing_id,
                quantity=terms.quantity,
                unit_price_cents=terms.unit_price_cents,
                variant_id=terms.variant_id,
            )
        for title, quantity, variant in lines:
            listing_id = self.listings.get((seller, title))
            if listing_id is None:
                return False
            chosen = None
            if listing_id in self.on_sale:
                listing = await self._fresh(listing_id)
                options = await orders.active_variants(self.db, listing_id)
                chosen = next((v for v in options.values() if v.name == variant), None)
                available = chosen.stock if chosen is not None else listing.stock
                if listing.status != ListingStatus.ACTIVE or available < quantity:
                    return False  # someone bought it between two runs of the load
            wanted.append(orders.Line(listing_id, quantity, chosen.id if chosen else None))
        checkout, _ = await orders.create_checkout(
            self.db, self.settings, self.registry, who, PROVIDER, wanted, f"demo-{key}", agreed
        )
        if any(line.listing_id in self.on_sale for line in wanted):
            await self._date_alerts()  # people who saved it may have been told stock is low
        self.open[key] = checkout
        self.checkout_dates[checkout.id] = {
            "expires": self.now + timedelta(minutes=self.settings.reservation_ttl_minutes),
            "paid": None,
            "closed": None,
        }
        return True

    async def _pay(
        self, key: int, buyer: str, method: str, installments: int | None, declined_first: bool
    ) -> bool:
        checkout = self.open.pop(key, None)
        if checkout is None:
            return False
        who, paid = self.users[buyer], self.now
        if declined_first:
            self.clock.now = paid - timedelta(minutes=1)
            refused = CardData("tok_declined", "visa", 1, who.email)
            await orders.start_payment(self.db, self.registry, checkout, who, "card", refused)
            self.clock.now = paid
        if method == "card":
            card = CardData(APPROVED_TEST_TOKEN, "visa", installments or 1, who.email)
            await orders.start_payment(
                self.db, self.registry, checkout, who, "card", card, self.settings.max_installments
            )
        else:
            attempt = await orders.start_payment(self.db, self.registry, checkout, who, "pix", None)
            self.provider.settle(attempt.provider_ref)
            status = await self.provider.get_status(attempt.provider_ref)
            await orders.apply_status(self.db, self.registry, attempt, status)
        self.checkout_dates[checkout.id]["paid"] = paid
        for order in checkout.orders:
            self.order_dates[order.id] = {"paid": paid}
            self.orders[key] = order
        return True

    async def _abandon(self, key: int, expired: bool) -> bool:
        checkout = self.open.pop(key, None)
        if checkout is None:
            return False
        await orders.close_unpaid(
            self.db,
            self.registry,
            checkout,
            final=CheckoutStatus.EXPIRED if expired else CheckoutStatus.CANCELLED,
            note="payment not received in time" if expired else "cancelled by buyer",
        )
        self.checkout_dates[checkout.id]["closed"] = self.now
        for order in checkout.orders:
            self.order_dates[order.id] = {"closed": self.now}
        return True

    async def _ship(self, key: int, seller: str, tracking: str) -> bool:
        order = self.orders.get(key)
        if order is None:
            return False
        await orders.mark_shipped(self.db, order, self.users[seller], tracking)
        self.order_dates[order.id]["shipped"] = self.now
        return True

    async def _deliver(self, key: int, buyer: str) -> bool:
        order = self.orders.get(key)
        if order is None:
            return False
        await orders.confirm_delivery(self.db, order, self.users[buyer])
        self.order_dates[order.id]["delivered"] = self.order_dates[order.id]["closed"] = self.now
        return True

    async def _review(self, key: int, buyer: str, rating: int, comment: str | None) -> bool:
        order = self.orders.get(key)
        if order is None:
            return False
        await reviews.create_review(
            order.id, reviews.ReviewIn(rating=rating, comment=comment), self.users[buyer], self.db
        )
        return True

    async def _refund(self, key: int, seller: str) -> bool:
        order = self.orders.get(key)
        if order is None:
            return False
        await orders.refund_order(self.db, self.registry, order, self.users[seller])
        self.order_dates[order.id]["closed"] = self.now
        return True

    # --- dates the services set to "now" -----------------------------------------------------

    async def _settle_dates(self) -> None:
        db = self.db
        await db.flush()
        for table, key, rows in (
            ("follows", ("follower_id", "followee_id"), self.follows),
            ("saves", ("user_id", "listing_id"), self.saves),
        ):
            await db.execute(
                text(
                    f"""
                    UPDATE {table} t SET created_at = v.at
                      FROM unnest(CAST(:a AS uuid[]), CAST(:b AS uuid[]),
                                  CAST(:at AS timestamptz[])) AS v(a, b, at)
                     WHERE t.{key[0]} = v.a AND t.{key[1]} = v.b
                    """  # noqa: S608 - table and column names are the two literals above
                ),
                {"a": [r[0] for r in rows], "b": [r[1] for r in rows], "at": [r[2] for r in rows]},
            )
        await db.execute(
            text(
                """
                UPDATE offers o
                   SET expires_at = o.created_at + interval '24 hours', responded_at = v.at
                  FROM unnest(CAST(:ids AS uuid[]), CAST(:at AS timestamptz[])) AS v(id, at)
                 WHERE o.id = v.id
                """
            ),
            {"ids": list(self.answered), "at": list(self.answered.values())},
        )
        await db.execute(
            text(
                """
                UPDATE questions q SET answered_at = v.at
                  FROM unnest(CAST(:ids AS uuid[]), CAST(:at AS timestamptz[])) AS v(id, at)
                 WHERE q.id = v.id
                """
            ),
            {"ids": list(self.answers), "at": list(self.answers.values())},
        )
        await db.execute(
            text(
                """
                UPDATE checkouts c
                   SET expires_at = v.expires, paid_at = v.paid, closed_at = v.closed
                  FROM unnest(CAST(:ids AS uuid[]), CAST(:expires AS timestamptz[]),
                              CAST(:paid AS timestamptz[]), CAST(:closed AS timestamptz[]))
                       AS v(id, expires, paid, closed)
                 WHERE c.id = v.id
                """
            ),
            {
                "ids": list(self.checkout_dates),
                **{
                    moment: [dates[moment] for dates in self.checkout_dates.values()]
                    for moment in ("expires", "paid", "closed")
                },
            },
        )
        await db.execute(
            text(
                """
                UPDATE orders o
                   SET paid_at = v.paid, shipped_at = v.shipped, delivered_at = v.delivered,
                       closed_at = v.closed
                  FROM unnest(CAST(:ids AS uuid[]), CAST(:paid AS timestamptz[]),
                              CAST(:shipped AS timestamptz[]), CAST(:delivered AS timestamptz[]),
                              CAST(:closed AS timestamptz[]))
                       AS v(id, paid, shipped, delivered, closed)
                 WHERE o.id = v.id
                """
            ),
            {
                "ids": list(self.order_dates),
                **{
                    moment: [dates.get(moment) for dates in self.order_dates.values()]
                    for moment in ("paid", "shipped", "delivered", "closed")
                },
            },
        )
        mine = {"ids": list(self.checkout_dates)}
        for statement in (
            """
            UPDATE ledger_entries e
               SET created_at = CASE WHEN e.kind IN ('sale', 'platform_fee')
                                     THEN o.paid_at ELSE o.closed_at END
              FROM orders o
             WHERE o.id = e.order_id AND o.checkout_id = ANY(:ids)
            """,
            """
            UPDATE stock_reservations r
               SET consumed_at = CASE WHEN r.consumed_at IS NULL THEN NULL ELSE c.paid_at END,
                   released_at = CASE WHEN r.released_at IS NULL THEN NULL ELSE c.closed_at END
              FROM checkouts c
             WHERE c.id = r.checkout_id AND c.id = ANY(:ids)
            """,
            "UPDATE payment_attempts SET updated_at = created_at WHERE checkout_id = ANY(:ids)",
            """
            UPDATE listings l SET updated_at = GREATEST(l.published_at, sold.at)
              FROM (SELECT i.listing_id, max(o.created_at) AS at
                      FROM order_items i JOIN orders o ON o.id = i.order_id
                     WHERE o.checkout_id = ANY(:ids) GROUP BY i.listing_id) sold
             WHERE l.id = sold.listing_id AND l.status = 'sold_out'
            """,
        ):
            await db.execute(text(statement), mine)
        # Safety net: a listing without a photo must never stay on sale.
        await db.execute(
            text(
                """
                UPDATE listings l SET status = 'removed'
                 WHERE l.seller_id = ANY(:users) AND l.status = 'active'
                   AND NOT EXISTS (SELECT 1 FROM listing_photos p WHERE p.listing_id = l.id)
                """
            ),
            {"users": [user.id for user in self.users.values()]},
        )


async def _replay(
    database: Database, settings: Settings, plan: Plan, anchor: datetime
) -> int | None:
    """The activity, in one transaction: all of it or none. Returns how many events were left
    out because their listing is no longer on sale, or None when the activity is already there."""
    async with database.sessionmaker() as db, db.begin():
        await _lock(db)
        demo = select(User.id).where(User.email.like(f"%@{DOMAIN}"))
        if await db.scalar(select(func.count()).where(Follow.follower_id.in_(demo))):
            return None
        with _simulated_time() as clock:
            replay = _Replay(db, settings, anchor, clock)
            await replay.prepare()
            await replay.play(plan)
        return replay.skipped


async def _pause_catalog(database: Database) -> int:
    async with database.sessionmaker() as db, db.begin():
        rows = await db.scalars(
            select(Listing)
            .join(User, User.id == Listing.seller_id)
            .where(User.email == CATALOG_SELLER_EMAIL, Listing.status == ListingStatus.ACTIVE)
        )
        paused = 0
        for listing in list(rows):
            await catalog.pause_listing(db, listing)
            paused += 1
        return paused


async def _anchor(database: Database, plan: Plan) -> datetime:
    """The moment the plan's ages count back from: now, or the moment of an earlier run that
    stopped before the activity, so its listings keep matching the plan."""
    now = datetime.now(UTC)
    async with database.sessionmaker() as db:
        rows = await db.execute(
            select(User.email, Listing.title, Listing.published_at)
            .join(User, User.id == Listing.seller_id)
            .where(User.email.like(f"%@{DOMAIN}"), Listing.status == ListingStatus.ACTIVE)
        )
        known = {(email, title): published for email, title, published in rows}
    for item in plan.items:
        published = known.get((item.seller, item.title))
        if published is not None:
            return min(now, published + timedelta(hours=item.published))
    return now


async def load(
    database: Database,
    storage: Storage,
    settings: Settings,
    *,
    size: Size | None = None,
    photo: Callable[[str], bytes] = cached_photo,
    keep_catalog: bool = False,
) -> dict[str, Any]:
    plan = build_plan(size)
    anchor = await _anchor(database, plan)
    await _accounts_and_listings(database, storage, settings, plan, anchor, photo)
    skipped = await _replay(database, settings, plan, anchor)
    paused = 0 if keep_catalog else await _pause_catalog(database)
    report: dict[str, Any] = {
        "activity": "already loaded" if skipped is None else "loaded",
        "events left out": skipped or 0,
        "catalog listings paused": paused,
    }
    async with database.sessionmaker() as db:
        report.update(await counts(db))
    return report


# --- counting and removing -------------------------------------------------------------------

_COUNTS = {
    "accounts": "SELECT count(*) FROM users WHERE id = ANY(:d)",
    "accounts with avatar": (
        "SELECT count(*) FROM users WHERE id = ANY(:d) AND avatar_key IS NOT NULL"
    ),
    "sellers": "SELECT count(DISTINCT seller_id) FROM listings WHERE seller_id = ANY(:d)",
    "listings on sale": (
        "SELECT count(*) FROM listings WHERE seller_id = ANY(:d) AND status = 'active'"
    ),
    "listings sold": (
        "SELECT count(*) FROM listings WHERE seller_id = ANY(:d) AND status = 'sold_out'"
    ),
    "photos": (
        "SELECT count(*) FROM listing_photos p JOIN listings l ON l.id = p.listing_id"
        " WHERE l.seller_id = ANY(:d)"
    ),
    "follows": "SELECT count(*) FROM follows WHERE follower_id = ANY(:d)",
    "saves": "SELECT count(*) FROM saves WHERE user_id = ANY(:d)",
    "questions": "SELECT count(*) FROM questions WHERE asker_id = ANY(:d)",
    "questions answered": (
        "SELECT count(*) FROM questions WHERE asker_id = ANY(:d) AND answer IS NOT NULL"
    ),
    "conversations": "SELECT count(*) FROM conversations WHERE buyer_id = ANY(:d)",
    "messages": (
        "SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id"
        " WHERE c.buyer_id = ANY(:d)"
    ),
    "checkouts": "SELECT count(*) FROM checkouts WHERE buyer_id = ANY(:d)",
    "orders": "SELECT count(*) FROM orders WHERE buyer_id = ANY(:d)",
    "reviews": "SELECT count(*) FROM reviews WHERE buyer_id = ANY(:d)",
    "ledger entries": "SELECT count(*) FROM ledger_entries WHERE seller_id = ANY(:d)",
    "notifications": "SELECT count(*) FROM notifications WHERE user_id = ANY(:d)",
}
_GROUPED = {
    "orders by status": (
        "SELECT status::text, count(*) FROM orders WHERE buyer_id = ANY(:d) GROUP BY 1"
    ),
    "offers by status": (
        "SELECT o.status::text, count(*) FROM offers o"
        " JOIN conversations c ON c.id = o.conversation_id WHERE c.buyer_id = ANY(:d) GROUP BY 1"
    ),
}


async def counts(db: AsyncSession) -> dict[str, Any]:
    """What the demo accounts own, by kind."""
    demo = {"d": list(await db.scalars(select(User.id).where(User.email.like(f"%@{DOMAIN}"))))}
    out: dict[str, Any] = {}
    for name, statement in _COUNTS.items():
        out[name] = int((await db.execute(text(statement), demo)).scalar_one())
    for name, statement in _GROUPED.items():
        rows = (await db.execute(text(statement), demo)).all()
        out[name] = dict(sorted((status, int(n)) for status, n in rows))
    return out


async def remove(database: Database, storage: Storage) -> dict[str, int]:
    """Delete the load: the demo accounts and everything that hangs from them."""
    keys: list[str] = []
    async with database.sessionmaker() as db, db.begin():
        await _lock(db)

        async def ids(statement: str, **params: Any) -> list[Any]:
            return list((await db.execute(text(statement), params)).scalars())

        async def run(statement: str, **params: Any) -> int:
            result = await db.execute(text(statement), params)
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

        demo = await ids("SELECT id FROM users WHERE email LIKE :mail", mail=f"%@{DOMAIN}")
        # Orders with an account outside the load stay, with the demo account and the
        # listings they name.
        shared = await ids(
            "SELECT id FROM orders WHERE (buyer_id = ANY(:d)) <> (seller_id = ANY(:d))", d=demo
        )
        kept_users = await ids(
            """
            SELECT DISTINCT u.id FROM users u JOIN orders o ON u.id IN (o.buyer_id, o.seller_id)
             WHERE o.id = ANY(:o) AND u.id = ANY(:d)
            """,
            o=shared,
            d=demo,
        )
        kept_listings = await ids(
            """
            SELECT DISTINCT i.listing_id FROM order_items i JOIN listings l ON l.id = i.listing_id
             WHERE i.order_id = ANY(:o) AND l.seller_id = ANY(:d)
            """,
            o=shared,
            d=demo,
        )
        users = [u for u in demo if u not in set(kept_users)]
        listings = await ids(
            "SELECT id FROM listings WHERE seller_id = ANY(:d) AND id <> ALL(:keep)",
            d=demo,
            keep=kept_listings,
        )
        for full, thumb in await db.execute(
            text("SELECT key_full, key_thumb FROM listing_photos WHERE listing_id = ANY(:l)"),
            {"l": listings},
        ):
            keys += [full, thumb]
        keys += await ids(
            "SELECT avatar_key FROM users WHERE id = ANY(:u) AND avatar_key IS NOT NULL", u=users
        )
        # Accounts and listings outside the load whose counters include a demo account.
        neighbours = await ids(
            """
            SELECT follower_id FROM follows WHERE followee_id = ANY(:u)
            UNION SELECT followee_id FROM follows WHERE follower_id = ANY(:u)
            """,
            u=users,
        )
        saved = await ids("SELECT DISTINCT listing_id FROM saves WHERE user_id = ANY(:u)", u=users)

        own = await ids(
            "SELECT id FROM orders WHERE buyer_id = ANY(:d) AND seller_id = ANY(:d)", d=demo
        )
        removed = {"accounts": len(users), "listings": len(listings), "orders": len(own)}
        await run("DELETE FROM reviews WHERE order_id = ANY(:o)", o=own)
        await run("DELETE FROM disputes WHERE order_id = ANY(:o)", o=own)
        await run("DELETE FROM ledger_entries WHERE order_id = ANY(:o)", o=own)
        await run("DELETE FROM orders WHERE id = ANY(:o)", o=own)
        empty = await ids(
            """
            SELECT c.id FROM checkouts c WHERE c.buyer_id = ANY(:d)
               AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.checkout_id = c.id)
            """,
            d=demo,
        )
        await run("DELETE FROM payment_attempts WHERE checkout_id = ANY(:c)", c=empty)
        await run("DELETE FROM checkouts WHERE id = ANY(:c)", c=empty)
        removed["conversations"] = await run(
            """
            DELETE FROM conversations
             WHERE listing_id = ANY(:l) OR (buyer_id = ANY(:d) AND seller_id = ANY(:d))
            """,
            l=listings,
            d=demo,
        )
        await run(
            """
            DELETE FROM reports
             WHERE (target_type = 'listing' AND target_id = ANY(:l))
                OR (target_type = 'user' AND target_id = ANY(:u))
            """,
            l=listings,
            u=users,
        )
        await run("DELETE FROM listings WHERE id = ANY(:l)", l=listings)
        await run(
            "UPDATE listings SET status = 'removed' WHERE id = ANY(:l) AND status <> 'removed'",
            l=kept_listings,
        )
        await run("DELETE FROM users WHERE id = ANY(:u)", u=users)
        removed["accounts kept for their orders"] = len(kept_users)
        await run(
            """
            UPDATE users u
               SET follower_count = (SELECT count(*) FROM follows f WHERE f.followee_id = u.id),
                   following_count = (SELECT count(*) FROM follows f WHERE f.follower_id = u.id)
             WHERE u.id = ANY(:u)
            """,
            u=neighbours + kept_users,
        )
        await run(
            """
            UPDATE listings l
               SET save_count = (SELECT count(*) FROM saves s WHERE s.listing_id = l.id)
             WHERE l.id = ANY(:l)
            """,
            l=saved + kept_listings,
        )
        # The single-seller catalog comes back; only this load ever pauses its listings.
        paused = await db.scalars(
            select(Listing)
            .join(User, User.id == Listing.seller_id)
            .where(User.email == CATALOG_SELLER_EMAIL, Listing.status == ListingStatus.PAUSED)
        )
        resumed = 0
        for listing in list(paused):
            if listing.photos and listing.stock > 0:
                await catalog.publish_listing(db, listing)
                resumed += 1
        removed["catalog listings resumed"] = resumed
    for key in keys:
        await storage.delete(key)
    removed["stored files"] = len(keys)
    return removed


# --- command line ----------------------------------------------------------------------------


def _print(title: str, report: dict[str, Any]) -> None:
    print(title)
    for name, value in report.items():
        print(f"  {name}: {value}")


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.env == "production" and not args.production:
        raise SystemExit("TP_ENV is production: pass --production to write to that database.")
    database = Database(settings)
    statements = 0

    def counted(*_: Any) -> None:
        nonlocal statements
        statements += 1

    event.listen(database.engine.sync_engine, "before_cursor_execute", counted)
    try:
        if args.remove or args.reset:
            _print("removed", await remove(database, build_storage(settings)))
        if not args.remove:
            report = await load(
                database, build_storage(settings), settings, keep_catalog=args.keep_catalog
            )
            _print("demo marketplace", report)
    finally:
        await database.dispose()
    print(f"{statements} statements")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--remove", action="store_true", help="delete the load")
    parser.add_argument("--reset", action="store_true", help="delete the load, then load again")
    parser.add_argument(
        "--keep-catalog",
        action="store_true",
        help="leave the single-seller catalog's listings on sale next to this load",
    )
    parser.add_argument(
        "--production", action="store_true", help="allow writing when TP_ENV is production"
    )
    asyncio.run(main(parser.parse_args()))
