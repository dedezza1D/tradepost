"""The demo marketplace load: a fixed plan, consistent counters, and a removal that spares
everything that is not part of it."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from scripts.seed_demo_catalog import SELLER_EMAIL as CATALOG_SELLER_EMAIL
from scripts.seed_demo_market import DATA, DOMAIN, Size, build_plan, load, remove
from tests.conftest import image_bytes, make_client, register
from tradepost.catalog import service as catalog
from tradepost.catalog.models import ItemCondition, Listing, ListingStatus
from tradepost.catalog.schemas import ListingCreate
from tradepost.chat.detection import warning_for
from tradepost.config import get_settings
from tradepost.identity.models import User
from tradepost.orders import service as orders
from tradepost.orders.models import Order, OrderStatus
from tradepost.payments.base import CardData, PaymentRegistry
from tradepost.payments.fake import APPROVED_TEST_TOKEN, FakeProvider
from tradepost.reviews.reputation import seller_reputation
from tradepost.social import service as social

SMALL = Size(shops=3, individuals=8, buyers=16)


def photo(_name: str) -> bytes:
    return image_bytes()


async def scalar(app: Any, statement: str, **params: Any) -> Any:
    async with app.state.db.sessionmaker() as db:
        return (await db.execute(text(statement), params)).scalar_one()


async def table_counts(app: Any) -> dict[str, int]:
    async with app.state.db.sessionmaker() as db:
        tables = await db.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        )
        out: dict[str, int] = {}
        for table in list(tables):
            rows = await db.execute(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
            out[table] = rows.scalar_one()
        return out


def stored_files() -> set[str]:
    root = Path(get_settings().media_local_dir)
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


async def run_load(app: Any) -> dict[str, Any]:
    return await load(app.state.db, app.state.storage, get_settings(), size=SMALL, photo=photo)


def test_the_plan_is_fixed_by_the_seed() -> None:
    assert build_plan(SMALL) == build_plan(SMALL)
    assert build_plan(SMALL, seed=7) != build_plan(SMALL)
    plan = build_plan()
    emails = [person.email for person in plan.people]
    assert len(set(emails)) == len(emails)
    assert all(email.endswith(f"@{DOMAIN}") for email in emails)
    assert len(plan.sellers) >= 30
    assert len({(item.seller, item.title) for item in plan.items}) == len(plan.items)
    assert {person.tier for person in plan.sellers} == {"top", "good", "attention", "new"}
    ages = [step.age for step in plan.events]
    assert ages == sorted(ages, reverse=True)
    assert min(ages) > 0
    # Weeks of history, with most of the follows and saves spread over many different days.
    assert max(ages) > 24 * 60
    assert len({int(step.age // 24) for step in plan.events if step.kind == "save"}) >= 20


def test_public_texts_pass_the_marketplace_rules() -> None:
    plan = build_plan()
    off_platform = {
        line
        for pair in json.loads(DATA.read_text("utf-8"))["chats"]["off_platform"]
        for line in pair
    }
    for step in plan.events:
        if step.kind in ("ask", "answer"):
            # Public questions refuse contact details and payment outside the platform.
            assert warning_for(step.data["body"]) is None, step.data["body"]
        if step.kind == "chat" and warning_for(step.data["body"]):
            assert step.data["body"] in off_platform
    moderation = pytest.importorskip("tradepost.moderation")
    listed = [(item.title, item.description) for item in plan.items]
    listed += [(s.data["title"], s.data["description"]) for s in plan.events if s.kind == "list"]
    for title, description in listed:
        assert moderation.listing_rules(title, description) is None, title
        assert "Anúncio de demonstração do tradepost" in description


async def test_loading_twice_adds_nothing_and_counters_match(app: Any) -> None:
    first = await run_load(app)
    assert first["activity"] == "loaded"
    assert first["events left out"] == 0
    assert first["follows"] > 0
    assert first["reviews"] > 0
    assert set(first["offers by status"]) >= {"accepted", "declined", "countered", "pending"}
    assert set(first["orders by status"]) >= {"delivered", "shipped"}
    before, files = await table_counts(app), stored_files()

    second = await run_load(app)
    assert second["activity"] == "already loaded"
    assert {**second, "activity": ""} == {**first, "activity": ""}
    assert await table_counts(app) == before
    assert stored_files() == files

    mismatches = {
        "follower counters": """
            SELECT count(*) FROM users u
             WHERE follower_count <> (SELECT count(*) FROM follows WHERE followee_id = u.id)
                OR following_count <> (SELECT count(*) FROM follows WHERE follower_id = u.id)
        """,
        "save counters": """
            SELECT count(*) FROM listings l
             WHERE save_count <> (SELECT count(*) FROM saves WHERE listing_id = l.id)
        """,
        "stock of listings with options": """
            SELECT count(*) FROM listings l
             WHERE EXISTS (SELECT 1 FROM listing_variants v WHERE v.listing_id = l.id AND v.active)
               AND stock <> (SELECT sum(stock) FROM listing_variants v
                              WHERE v.listing_id = l.id AND v.active)
        """,
        "status against stock": """
            SELECT count(*) FROM listings
             WHERE (status = 'active' AND stock = 0) OR (status = 'sold_out' AND stock > 0)
        """,
        "listings on sale without a photo": """
            SELECT count(*) FROM listings l
             WHERE status = 'active'
               AND NOT EXISTS (SELECT 1 FROM listing_photos p WHERE p.listing_id = l.id)
        """,
        "paid orders without a sale in the ledger": """
            SELECT count(*) FROM orders o
             WHERE status IN ('paid', 'shipped', 'delivered', 'refunded')
               AND NOT EXISTS (SELECT 1 FROM ledger_entries e
                                WHERE e.order_id = o.id AND e.kind = 'sale'
                                  AND e.amount_cents = o.total_cents AND e.created_at = o.paid_at)
        """,
        "units sold against reservations": """
            SELECT count(*) FROM (
                SELECT i.listing_id FROM order_items i JOIN orders o ON o.id = i.order_id
                 WHERE o.status IN ('paid', 'shipped', 'delivered', 'refunded')
                 GROUP BY i.listing_id
                HAVING sum(i.quantity) <> (SELECT coalesce(sum(r.quantity), 0)
                                             FROM stock_reservations r
                                            WHERE r.listing_id = i.listing_id
                                              AND r.consumed_at IS NOT NULL)) x
        """,
        "reviews of orders not delivered": """
            SELECT count(*) FROM reviews r JOIN orders o ON o.id = r.order_id
             WHERE o.status <> 'delivered' OR r.created_at < o.delivered_at
        """,
        "order dates out of order": """
            SELECT count(*) FROM orders
             WHERE paid_at < created_at OR shipped_at < paid_at OR delivered_at < shipped_at
        """,
        "answers before their question": """
            SELECT count(*) FROM questions WHERE answered_at < created_at
        """,
        "follows before the account existed": """
            SELECT count(*) FROM follows f
              JOIN users a ON a.id = f.follower_id JOIN users b ON b.id = f.followee_id
             WHERE f.created_at < greatest(a.created_at, b.created_at)
        """,
        "saves before the listing was published": """
            SELECT count(*) FROM saves s JOIN listings l ON l.id = s.listing_id
             WHERE s.created_at < l.published_at
        """,
        "messages out of order": """
            SELECT count(*) FROM messages m
             WHERE EXISTS (SELECT 1 FROM messages n
                            WHERE n.conversation_id = m.conversation_id
                              AND n.id > m.id AND n.created_at < m.created_at)
        """,
        "notifications out of order": """
            SELECT count(*) FROM notifications m
             WHERE EXISTS (SELECT 1 FROM notifications n
                            WHERE n.user_id = m.user_id
                              AND n.id > m.id AND n.created_at < m.created_at)
        """,
        "rows dated at the load instead of at their event": """
            SELECT sum(n) FROM (
                SELECT count(*) AS n FROM notifications WHERE created_at > now() - interval '1 min'
                UNION ALL SELECT count(*) FROM messages WHERE created_at > now() - interval '1 min'
                UNION ALL SELECT count(*) FROM follows WHERE created_at > now() - interval '1 min'
                UNION ALL SELECT count(*) FROM saves WHERE created_at > now() - interval '1 min'
                UNION ALL SELECT count(*) FROM orders WHERE created_at > now() - interval '1 min'
                UNION ALL SELECT count(*) FROM ledger_entries
                           WHERE created_at > now() - interval '1 min') x
        """,
        "offers with impossible dates": """
            SELECT count(*) FROM offers
             WHERE (status = 'pending' AND (expires_at <= now() OR responded_at IS NOT NULL))
                OR (status <> 'pending'
                    AND (responded_at IS NULL OR responded_at > expires_at
                         OR responded_at < created_at))
        """,
        "accounts that could sign in": """
            SELECT count(*) FROM users WHERE password_hash <> '!' OR municipality_id IS NULL
        """,
    }
    for what, statement in mismatches.items():
        assert await scalar(app, statement) == 0, what
    assert await scalar(app, "SELECT count(DISTINCT created_at::date) FROM follows") >= 10

    plan = build_plan(SMALL)
    async with app.state.db.sessionmaker() as db:
        users = {
            user.email: user
            for user in await db.scalars(select(User).where(User.email.like(f"%@{DOMAIN}")))
        }
        assert len(users) == len(plan.people)
        # Each seller's orders, delays and ratings add up to the reputation planned for them.
        for person in plan.sellers:
            reputation = await seller_reputation(db, users[person.email].id)
            assert reputation.level == person.tier, person.email
        trending = await social.feed(
            db, app.state.storage, None, "trending", cursor=None, limit=12, near=None, radius_km=50
        )
        assert len(trending.items) == 12
        assert trending.items[0].save_count > 0
        assert all(card.thumb_url for card in trending.items)
        follower = next(
            users[step.data["follower"]] for step in plan.events if step.kind == "follow"
        )
        following = await social.feed(
            db,
            app.state.storage,
            follower,
            "following",
            cursor=None,
            limit=12,
            near=None,
            radius_km=50,
        )
        assert following.items


async def test_demo_accounts_cannot_sign_in_or_be_registered(app: Any) -> None:
    await run_load(app)
    address = build_plan(SMALL).people[0].email
    async with make_client(app) as client:
        for password in ("!", "correct horse battery"):
            response = await client.post(
                "/api/auth/login", json={"email": address, "password": password}
            )
            assert response.status_code in (401, 422)
        # Nobody can sign up under the domain that marks the load, so removal never takes a
        # real account for a demo one.
        response = await client.post(
            "/api/auth/register",
            json={
                "email": f"someone@{DOMAIN}",
                "password": "correct horse battery",
                "display_name": "Someone",
            },
        )
        assert response.status_code == 422


async def test_removal_spares_what_is_not_part_of_the_load(app: Any) -> None:
    settings, database, storage = get_settings(), app.state.db, app.state.storage
    registry = PaymentRegistry([FakeProvider()])
    async with database.sessionmaker() as db, db.begin():
        owner = User(
            email=CATALOG_SELLER_EMAIL,
            password_hash="!",
            display_name="Catálogo de demonstração",
            city="São Paulo",
            state="SP",
        )
        db.add(owner)
        await db.flush()
        single = await catalog.create_listing(
            db,
            owner,
            ListingCreate(
                title="Bicicleta antiga vermelha",
                description="Bicicleta para passeios e para o dia a dia na cidade.",
                category_slug="bikes",
                condition=ItemCondition.USED,
                price_cents=323000,
                stock=1,
                city="São Paulo",
                state="SP",
            ),
        )
        await db.refresh(single, ["photos"])
        await catalog.add_photo(db, storage, settings, single, image_bytes())
        await catalog.publish_listing(db, single)
    async with make_client(app) as client:
        visitor_email = (await register(client, display_name="Visitante"))["email"]
    untouched = stored_files()

    await run_load(app)
    assert await scalar(app, "SELECT status::text FROM listings WHERE id = :id", id=single.id) == (
        "paused"
    )
    # A real account follows a demo seller, saves a listing and buys another one.
    async with database.sessionmaker() as db, db.begin():
        visitor = (await db.scalars(select(User).where(User.email == visitor_email))).one()
        demo = select(User.id).where(User.email.like(f"%@{DOMAIN}"))
        on_sale = list(
            await db.scalars(
                select(Listing)
                .where(
                    Listing.seller_id.in_(demo),
                    Listing.status == ListingStatus.ACTIVE,
                    Listing.stock == 1,
                )
                .order_by(Listing.title)
                .limit(2)
            )
        )
        bought, saved = on_sale
        await social.follow(db, visitor, saved.seller_id)
        await social.follow(db, visitor, bought.seller_id)
        await social.save_listing(db, visitor, saved.id)
        checkout, _ = await orders.create_checkout(
            db, settings, registry, visitor, "fake", [orders.Line(bought.id, 1)], "visitor-1"
        )
        card = CardData(APPROVED_TEST_TOKEN, "visa", 1, visitor.email)
        await orders.start_payment(db, registry, checkout, visitor, "card", card)
        order_id = checkout.orders[0].id
    assert (
        await scalar(app, "SELECT following_count FROM users WHERE email = :e", e=visitor_email)
        == 2
    )

    removed = await remove(database, storage)
    assert removed["accounts kept for their orders"] == 1
    assert removed["catalog listings resumed"] == 1
    async with database.sessionmaker() as db:
        visitor = (await db.scalars(select(User).where(User.email == visitor_email))).one()
        # The visitor still follows the seller they bought from, and nobody else.
        assert (visitor.following_count, visitor.follower_count) == (1, 0)
        order = await db.get(Order, order_id)
        assert order is not None
        assert order.status == OrderStatus.PAID
        kept = list(await db.scalars(select(User).where(User.email.like(f"%@{DOMAIN}"))))
        assert [user.id for user in kept] == [order.seller_id]
        assert (kept[0].follower_count, kept[0].following_count) == (1, 0)
        listings = list(await db.scalars(select(Listing).where(Listing.seller_id == kept[0].id)))
        assert [(item.id, item.status) for item in listings] == [(bought.id, ListingStatus.REMOVED)]
        assert (await db.get(Listing, single.id)).status == ListingStatus.ACTIVE  # type: ignore[union-attr]
    counts = await table_counts(app)
    assert counts["users"] == 3  # the catalog's seller, the visitor and the seller kept
    assert counts["orders"] == counts["checkouts"] == 1
    assert counts["follows"] == 1
    for table in ("saves", "questions", "conversations", "messages", "offers", "reviews"):
        assert counts[table] == 0, table
    assert (
        await scalar(
            app,
            "SELECT count(*) FROM notifications WHERE user_id <> ALL(:keep)",
            keep=[visitor.id, kept[0].id],
        )
        == 0
    )
    # Only what belongs to the seller kept for the visitor's order stays in storage: the
    # two renditions of that listing's photo, and the seller's avatar when there is one.
    assert len(stored_files() - untouched) == 2 + (kept[0].avatar_key is not None)

    # The load can be put back on top of what was kept.
    again = await run_load(app)
    assert again["activity"] == "loaded"
    assert again["follows"] > 0
    assert (
        await scalar(
            app,
            """
        SELECT count(*) FROM users u
         WHERE follower_count <> (SELECT count(*) FROM follows WHERE followee_id = u.id)
            OR following_count <> (SELECT count(*) FROM follows WHERE follower_id = u.id)
        """,
        )
        == 0
    )
    assert (await remove(database, storage))["accounts kept for their orders"] == 1
