#!/usr/bin/env python3
"""Seed GTM Hackathon demo data into Firestore for a user (by email).

The email comes from the first argument, or from WARMTH_DEMO_USER_EMAIL when
the argument is omitted.

Usage:
  export WARMTH_DEMO_USER_EMAIL=<firebase-user-email>
  uv run python scripts/migrate_user_demo_to_firestore.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv()

from packages.core.secrets import load_secrets_into_env  # noqa: E402

load_secrets_into_env()

os.environ.setdefault("GCP_PROJECT_ID", "warmth-gtm-hackathon")


def _ensure_warmth_namespace() -> None:
    """Match the Cloud Run layout so infra's relative imports resolve."""
    if "warmth" in sys.modules:
        return
    init = Path(ROOT) / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "warmth",
        init,
        submodule_search_locations=[ROOT],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load the warmth package")
    module = importlib.util.module_from_spec(spec)
    sys.modules["warmth"] = module
    spec.loader.exec_module(module)


_ensure_warmth_namespace()

from firebase_admin import auth as firebase_auth  # noqa: E402
from firebase_admin import firestore as firebase_firestore  # noqa: E402

from warmth.infra.firebase.admin import ensure_firebase_initialized  # noqa: E402
from warmth.infra.firebase.user_store_repo import UserStoreRepository  # noqa: E402
from packages.core.models.event import DetectedEvent, EventType, LifecycleStage  # noqa: E402
from packages.core.models.pre_connection import PreMeetConnection, PreMeetStatus  # noqa: E402
from packages.core.models.warmth import WarmthScore, WarmthBand  # noqa: E402
from packages.core.demo_user import demo_user_email  # noqa: E402
from packages.core.models.user_profile import UserProfile  # noqa: E402

GTM_EVENT_ID = "event_gtm_hackathon_london"
GTM_DATA_FILE = Path(ROOT) / "data" / "gtm_hackathon_attendees.json"
GTM_ATTENDEE_IDS = {
    "molyleelatham@gmail.com": "premeet_moly_leelatham",
    "nicholasyswong@googlemail.com": "premeet_nicholas_wong",
}
GTM_SCORES = {
    "molyleelatham@gmail.com": {"icp": 88, "warmth": 82, "intent": 75},
    "nicholasyswong@googlemail.com": {"icp": 76, "warmth": 68, "intent": 60},
}
_DEFAULT_GTM_SCORES = {"icp": 65, "warmth": 50, "intent": 45}


def _notes_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    text = str(raw).strip()
    return [text] if text else []


def _build_demo_store(user_id: str) -> SimpleNamespace:
    attendees = json.loads(GTM_DATA_FILE.read_text()) if GTM_DATA_FILE.exists() else []
    event = DetectedEvent(
        id=GTM_EVENT_ID,
        user_id=user_id,
        name="GTM Hackathon London",
        event_type=EventType.EVENT,
        location="The Building Centre",
        start_date=datetime(2026, 6, 20, 8, 30),
        end_date=datetime(2026, 6, 20, 18, 0),
        confidence=1.0,
        stage=LifecycleStage.BEFORE_MEET,
        attendee_count=len(attendees),
    )
    events = {event.id: event}
    pre_connections = {}
    warmth = {}
    for att in attendees:
        email = (att.get("email") or "").strip().lower()
        scores = GTM_SCORES.get(email, _DEFAULT_GTM_SCORES) if email else _DEFAULT_GTM_SCORES
        if email:
            conn_id = GTM_ATTENDEE_IDS.get(email, f"premeet_{email.replace('@', '_')}")
        else:
            slug = re.sub(r"[^a-z0-9]+", "_", (att.get("name") or "attendee").lower()).strip("_")
            conn_id = f"premeet_{slug or 'attendee'}"
        conn = PreMeetConnection(
            id=conn_id,
            event_id=event.id,
            user_id=user_id,
            name=att.get("name"),
            email=att.get("email"),
            linkedin=att.get("linkedin"),
            industry="GTM / SaaS",
            interests=att.get("interests") or [],
            research_notes=_notes_list(att.get("research_notes")),
            icp_score=scores["icp"],
            predicted_warmth=scores["warmth"],
            intent_score=scores["intent"],
            status=PreMeetStatus.SCORED,
            source=att.get("source", "calendar+tavily"),
        )
        pre_connections[conn.id] = conn
        warmth[conn.id] = WarmthScore(
            connection_id=conn.id,
            icp_score=int(conn.icp_score),
            warmth_score=conn.predicted_warmth,
            predicted_score=conn.predicted_warmth,
            band=WarmthBand.HOT if conn.predicted_warmth >= 70 else WarmthBand.WARM,
        )
    community_members = [
        {"user_id": "founder_amir", "name": "Amir", "interests": ["RevOps", "AI", "GTM"]},
        {"user_id": "friend_sara", "name": "Sara", "interests": ["fintech", "automation"]},
        {"user_id": "founder_lena", "name": "Lena", "interests": ["developer experience", "growth"]},
    ]
    return SimpleNamespace(
        events=events,
        pre_connections=pre_connections,
        warmth=warmth,
        meet_results={},
        leads={},
        signal_index={},
        gtm_sync_results={},
        community_members=community_members,
        list_events=lambda uid: [e for e in events.values() if e.user_id == uid],
        list_connections=lambda uid: [c for c in pre_connections.values() if c.user_id == uid],
        warmth_for_connection=lambda cid: warmth.get(cid),
        meet_result_for=lambda cid: None,
    )


def resolve_owner_email(argv: list[str] | None = None) -> str:
    """Email from the CLI, otherwise WARMTH_DEMO_USER_EMAIL."""
    args = list(sys.argv[1:] if argv is None else argv)
    chosen = args[0] if args else demo_user_email()
    email = (chosen or "").strip().lower()
    if not email or "@" not in email:
        raise SystemExit(
            "Set WARMTH_DEMO_USER_EMAIL or pass the Firebase Auth email as the first argument."
        )
    return email


def main() -> None:
    email = resolve_owner_email()
    print(f"Migrating demo roster for {email}...")
    ensure_firebase_initialized()
    user = firebase_auth.get_user_by_email(email)
    uid = user.uid
    print(f"Firebase uid={uid}")

    store = _build_demo_store(uid)
    repo = UserStoreRepository(firebase_firestore.client())
    repo.persist_snapshot(uid, store)

    firebase_firestore.client().collection("users").document(uid).set(
        UserProfile(
            uid=uid,
            email=user.email,
            display_name=user.display_name,
            photo_url=user.photo_url,
            demo_seeded=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ).model_dump(mode="json"),
        merge=True,
    )

    print(f"Done: {len(store.list_events(uid))} event(s), {len(store.list_connections(uid))} connection(s).")
    for c in store.list_connections(uid):
        print(f"  - {c.name} ({c.email}) warmth={c.predicted_warmth}")


if __name__ == "__main__":
    main()
