"""Forward Stripe sandbox webhooks to the local API, using the test key from backend/.env.

    uv run python scripts/stripe_listen.py [path-to-stripe-cli]

The key is passed through the environment, so it never shows up in the process list.
"""

import os
import shutil
import subprocess
import sys

from tradepost.config import get_settings

key = get_settings().stripe_secret_key
if key is None or not key.get_secret_value().startswith("sk_test_"):
    sys.exit("TP_STRIPE_SECRET_KEY must be a test key (sk_test_...).")

cli = sys.argv[1] if len(sys.argv) > 1 else shutil.which("stripe")
if not cli:
    sys.exit("Stripe CLI not found: pass its path as the first argument.")

env = {**os.environ, "STRIPE_API_KEY": key.get_secret_value()}
events = ",".join(
    [
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
        "payment_intent.processing",
        "payment_intent.requires_action",
        "charge.refunded",
    ]
)
sys.exit(
    subprocess.call(  # noqa: S603 - fixed arguments, local developer tool
        [cli, "listen", "--events", events, "--forward-to", "localhost:8000/api/webhooks/stripe"],
        env=env,
    )
)
