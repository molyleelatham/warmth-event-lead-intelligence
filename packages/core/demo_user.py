"""Which Firebase Auth account receives the seeded demo roster."""

from __future__ import annotations

import os

DEMO_USER_EMAIL_ENV = "WARMTH_DEMO_USER_EMAIL"


def demo_user_email() -> str:
    """Return the configured demo-user email, or an empty string when unset.

    An empty value means no account is treated as the demo user.
    """
    return os.getenv(DEMO_USER_EMAIL_ENV, "").strip().lower()
