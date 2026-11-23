"""Imports every model so ``Base.metadata`` is complete (Alembic and tests rely on it)."""

from tradepost.ai.models import AICache, AICall
from tradepost.analytics import ListingDailyViews
from tradepost.catalog.models import Category, Listing, ListingPhoto, ListingVariant
from tradepost.catalog.semantic import ListingEmbedding
from tradepost.chat.models import Conversation, Message, Offer
from tradepost.coupons import Coupon
from tradepost.db import Base
from tradepost.disputes import Dispute, DisputeMessage
from tradepost.fx import FxRate
from tradepost.geo.models import Municipality
from tradepost.identity.models import (
    PasswordResetToken,
    RecoveryCode,
    Session,
    TotpFactor,
    User,
)
from tradepost.notifications.delivery import (
    NotificationDelivery,
    NotificationPreference,
    PushSubscriptionRow,
)
from tradepost.notifications.models import Notification
from tradepost.ops.models import AlertState, ErrorEvent, FeatureFlag, JobRun, RequestStat
from tradepost.orders.models import (
    CartItem,
    Checkout,
    LedgerEntry,
    Order,
    OrderEvent,
    OrderItem,
    PaymentAttempt,
    PaymentEvent,
    SellerAccount,
    StockReservation,
)
from tradepost.questions import Question
from tradepost.reviews.service import Review
from tradepost.saved_searches import SavedSearch
from tradepost.security.ratelimit import RateLimitBucket
from tradepost.social.models import Block, Follow, Report, Save

__all__ = [
    "AICache",
    "AICall",
    "AlertState",
    "Base",
    "Block",
    "CartItem",
    "Category",
    "Checkout",
    "Conversation",
    "Coupon",
    "Dispute",
    "DisputeMessage",
    "ErrorEvent",
    "FeatureFlag",
    "Follow",
    "FxRate",
    "JobRun",
    "LedgerEntry",
    "Listing",
    "ListingDailyViews",
    "ListingEmbedding",
    "ListingPhoto",
    "ListingVariant",
    "Message",
    "Municipality",
    "Notification",
    "NotificationDelivery",
    "NotificationPreference",
    "Offer",
    "Order",
    "OrderEvent",
    "OrderItem",
    "PasswordResetToken",
    "PaymentAttempt",
    "PaymentEvent",
    "PushSubscriptionRow",
    "Question",
    "RateLimitBucket",
    "RecoveryCode",
    "Report",
    "RequestStat",
    "Review",
    "Save",
    "SavedSearch",
    "SellerAccount",
    "Session",
    "StockReservation",
    "TotpFactor",
    "User",
]
