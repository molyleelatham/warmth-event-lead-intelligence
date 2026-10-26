"""Bootstrap Firestore user profiles and seed demo dashboard data on first sign-in."""

from __future__ import annotations

from datetime import datetime

from firebase_admin import auth as firebase_auth

from ...infra.firebase.firestore import FirestoreClient
from ...packages.core.demo_user import demo_user_email
from ...packages.core.models.user_profile import UserProfile
from ..api.store import get_store


def maybe_seed_owner_demo(uid: str, email: str | None) -> None:
    """Ensure the GTM Hackathon demo roster exists for the configured account.

    The account comes from ``WARMTH_DEMO_USER_EMAIL``. When that variable is
    unset, nobody is seeded.
    """
    owner = demo_user_email()
    if not owner:
        return
    resolved = (email or "").strip().lower()
    if not resolved:
        try:
            import firebase_admin

            if firebase_admin._apps:
                record = firebase_auth.get_user(uid)
                resolved = (record.email or "").strip().lower()
        except Exception:
            return
    if resolved != owner:
        return
    get_store(uid).ensure_user_seed(uid)


class UserBootstrapService:
    """Upsert profile from Firebase Auth and ensure per-user demo roster."""

    def __init__(self, firestore: FirestoreClient | None) -> None:
        self.firestore = firestore

    async def bootstrap(self, uid: str) -> UserProfile:
        record = firebase_auth.get_user(uid)
        now = datetime.utcnow()
        existing = None
        if self.firestore:
            existing = await self.firestore.get_user_profile(uid)

        profile = UserProfile(
            uid=uid,
            email=record.email,
            display_name=record.display_name,
            photo_url=record.photo_url,
            demo_seeded=existing.demo_seeded if existing else False,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

        # Profile always comes from Google Auth. The demo roster is attached
        # only when this account matches WARMTH_DEMO_USER_EMAIL.
        owner = demo_user_email()
        email = (record.email or "").strip().lower()
        store = get_store(uid)
        if owner and email == owner:
            store.ensure_user_seed(uid)
            profile.demo_seeded = True
        elif existing is None and not profile.demo_seeded:
            profile.demo_seeded = False

        if self.firestore:
            profile = await self.firestore.upsert_user_profile(profile)

        return profile

    async def get_profile(self, uid: str) -> UserProfile | None:
        if not self.firestore:
            return None
        return await self.firestore.get_user_profile(uid)
