"""In-memory demo store.

A lightweight, process-local data store seeded with sample data so the web
dashboard and API are demoable without Firebase or external credentials.

GTM Hackathon roster is loaded from ``warmth/data/gtm_hackathon_attendees.json``
when present (written by ``scripts/run_gtm_hackathon_pull.py``).
"""
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import json
import re

from ...packages.core.models.event import DetectedEvent, EventType, LifecycleStage
from ...packages.core.models.pre_connection import PreMeetConnection, PreMeetStatus
from ...packages.core.models.lead import Lead
from ...packages.core.models.warmth import WarmthScore, WarmthBand


DEMO_USER_ID = "demo-user"
GTM_EVENT_ID = "event_gtm_hackathon_london"
GTM_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "gtm_hackathon_attendees.json"

# Stable ids so dashboard links survive refreshes
GTM_ATTENDEE_IDS = {
    "molyleelatham@gmail.com": "premeet_moly_leelatham",
    "nicholasyswong@googlemail.com": "premeet_nick_wong",
}

GTM_SCORES = {
    "molyleelatham@gmail.com": {"icp": 88, "warmth": 82, "intent": 75, "band": WarmthBand.HOT},
    "nicholasyswong@googlemail.com": {"icp": 76, "warmth": 68, "intent": 60, "band": WarmthBand.WARM},
}

_DEFAULT_GTM_SCORES = {"icp": 65, "warmth": 50, "intent": 45, "band": WarmthBand.WARM}


def _attendee_connection_id(email: str, name: str | None) -> str:
    """Stable connection id. Attendees without an email are keyed by name."""
    if email:
        return GTM_ATTENDEE_IDS.get(email, f"premeet_{email.replace('@', '_')}")
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "attendee").lower()).strip("_")
    return f"premeet_{slug or 'attendee'}"


def _notes_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    text = str(raw).strip()
    return [text] if text else []


def _company_from_attendee(att: dict[str, Any]) -> Optional[str]:
    if att.get("company") or att.get("company_name"):
        return att.get("company") or att.get("company_name")
    notes = _notes_list(att.get("research_notes"))
    blob = " ".join(notes)
    for pat in (
        r"@\s*([A-Z][A-Za-z0-9&\s]+?)(?:\s+\||\s+\.\s|\s*\n|$)",
        r"Co-Founder @\s*(\w+)",
        r"Director @(\w+)",
    ):
        m = re.search(pat, blob)
        if m:
            return m.group(1).strip()
    if "Imperial College" in blob:
        return "Imperial College London"
    if "Fynco" in blob:
        return "Fynco"
    if "CLARK" in blob:
        return "CLARK"
    if "Aldzama" in blob:
        return "Aldzama"
    return None


def _title_from_attendee(att: dict[str, Any]) -> Optional[str]:
    if att.get("title"):
        return att["title"]
    notes = _notes_list(att.get("research_notes"))
    if not notes:
        return None
    head = notes[0].split("|")[0].strip()
    if " - " in head:
        return head.split(" - ", 1)[1][:80]
    return None


class DemoStore:
    def __init__(self, *, seed: bool = True):
        self.events: dict[str, DetectedEvent] = {}
        self.pre_connections: dict[str, PreMeetConnection] = {}
        self.leads: dict[str, Lead] = {}
        self.warmth: dict[str, WarmthScore] = {}
        self.gtm_sync_results: dict[str, Any] = {}
        self.community_members: list[dict] = []
        self.meet_results: dict[str, dict[str, Any]] = {}  # connection_id -> last meet result
        self.knowledge_graphs: dict[str, dict[str, Any]] = {}  # connection_id -> KG payload
        self.signal_index: dict[str, str] = {}  # ios signal uuid -> connection_id
        if seed:
            self._seed()

    # ------------------------------------------------------------------ #
    def _seed(self) -> None:
        self.refresh_gtm_hackathon()

    def refresh_gtm_hackathon(
        self,
        attendees: Optional[list[dict[str, Any]]] = None,
        *,
        user_id: str = DEMO_USER_ID,
        event_name: str = "GTM Hackathon London",
        event_location: str = "The Building Centre",
        premeet_results: Optional[list[PreMeetConnection]] = None,
        sync_results: Optional[dict[str, Any]] = None,
    ) -> DetectedEvent:
        """Load or refresh the GTM Hackathon dashboard roster."""
        if attendees is None and GTM_DATA_FILE.exists():
            attendees = json.loads(GTM_DATA_FILE.read_text())

        now = datetime.utcnow()
        event = DetectedEvent(
            id=GTM_EVENT_ID,
            user_id=user_id,
            name=event_name,
            event_type=EventType.EVENT,
            location=event_location,
            start_date=datetime(2026, 6, 20, 8, 30),
            end_date=datetime(2026, 6, 20, 18, 0),
            confidence=1.0,
            stage=LifecycleStage.BEFORE_MEET,
            attendee_count=len(attendees or []),
            premeet_completed=bool(premeet_results),
        )
        self.events = {event.id: event}

        # Drop old connections for this event
        old_ids = [k for k, v in self.pre_connections.items() if v.event_id == GTM_EVENT_ID]
        for kid in old_ids:
            self.pre_connections.pop(kid, None)
            self.warmth.pop(kid, None)

        results_by_email = {}
        if premeet_results:
            for c in premeet_results:
                if c.email:
                    results_by_email[c.email.lower()] = c

        if not attendees:
            attendees = []

        for att in attendees:
            email = (att.get("email") or "").strip().lower()
            scores = GTM_SCORES.get(email, _DEFAULT_GTM_SCORES) if email else _DEFAULT_GTM_SCORES
            merged = results_by_email.get(email)
            conn_id = _attendee_connection_id(email, att.get("name"))

            conn = PreMeetConnection(
                id=conn_id,
                event_id=event.id,
                user_id=user_id,
                name=att.get("name"),
                email=att.get("email") or None,
                title=_title_from_attendee(att) or (merged.title if merged else None),
                linkedin=att.get("linkedin") or (merged.linkedin if merged else None),
                company_name=_company_from_attendee(att) or (merged.company_name if merged else None),
                company_domain=att.get("company_domain"),
                industry=att.get("industry") or (merged.industry if merged else None) or "GTM / SaaS",
                interests=att.get("interests") or [],
                research_notes=_notes_list(att.get("research_notes")),
                icp_score=merged.icp_score if merged else scores["icp"],
                predicted_warmth=merged.predicted_warmth if merged else scores["warmth"],
                intent_score=merged.intent_score if merged else scores["intent"],
                draft_subject=merged.draft_subject if merged else None,
                draft_body=merged.draft_body if merged else None,
                gmail_draft_id=merged.gmail_draft_id if merged else None,
                status=merged.status if merged else PreMeetStatus.SCORED,
                source=att.get("source", "calendar+tavily"),
            )
            self.pre_connections[conn.id] = conn
            band = WarmthBand.HOT if conn.predicted_warmth >= 70 else WarmthBand.WARM
            self.warmth[conn.id] = WarmthScore(
                connection_id=conn.id,
                icp_score=int(conn.icp_score),
                warmth_score=conn.predicted_warmth,
                predicted_score=conn.predicted_warmth,
                band=band,
            )

        if sync_results is not None:
            hubspot = dict(sync_results.get("hubspot", {}))
            zero = dict(sync_results.get("zero", {}))
            hubspot.pop("contacts", None)
            self.gtm_sync_results = {
                "hubspot": hubspot,
                "zero": zero,
                "updated_at": datetime.utcnow().isoformat(),
            }

        self.community_members = [
            {"user_id": "founder_amir", "name": "Amir", "interests": ["RevOps", "AI", "GTM"]},
            {"user_id": "friend_sara", "name": "Sara", "interests": ["fintech", "automation"]},
            {"user_id": "founder_lena", "name": "Lena", "interests": ["developer experience", "growth"]},
        ]
        return event

    def ensure_user_seed(self, user_id: str) -> None:
        """Seed GTM hackathon demo roster for a user with no events yet."""
        if self.list_events(user_id):
            return
        self.refresh_gtm_hackathon(user_id=user_id)

    def list_connections(self, user_id: Optional[str] = None) -> list[PreMeetConnection]:
        items = list(self.pre_connections.values())
        if user_id:
            items = [c for c in items if c.user_id == user_id]
        return items

    # ------------------------------------------------------------------ #
    def list_events(self, user_id: Optional[str] = None) -> list[DetectedEvent]:
        items = list(self.events.values())
        if user_id:
            items = [e for e in items if e.user_id == user_id]
        return items

    def get_event(self, event_id: str) -> Optional[DetectedEvent]:
        return self.events.get(event_id)

    def upsert_event(self, event: DetectedEvent) -> DetectedEvent:
        self.events[event.id] = event
        return event

    def connections_for_event(self, event_id: str) -> list[PreMeetConnection]:
        return [c for c in self.pre_connections.values() if c.event_id == event_id]

    def upsert_connection(self, conn: PreMeetConnection) -> PreMeetConnection:
        self.pre_connections[conn.id] = conn
        return conn

    def list_leads(self) -> list[Lead]:
        return list(self.leads.values())

    def upsert_lead(self, lead: Lead) -> Lead:
        self.leads[lead.id] = lead
        return lead

    def warmth_for_connection(self, connection_id: str) -> Optional[WarmthScore]:
        return self.warmth.get(connection_id)

    def upsert_warmth(self, score: WarmthScore) -> WarmthScore:
        key = score.connection_id or score.id
        self.warmth[key] = score
        return score

    def get_connection(self, connection_id: str) -> Optional[PreMeetConnection]:
        return self.pre_connections.get(connection_id)

    def record_meet_result(
        self,
        connection_id: str,
        signal_id: str,
        routed_to: str,
        narrative: Optional[str] = None,
        gmail_draft: Optional[dict] = None,
        outreach_sequence: Optional[dict] = None,
        *,
        interests: Optional[list[str]] = None,
        relations: Optional[list[dict]] = None,
        knowledge_graph: Optional[list[dict]] = None,
        matched_candidates: Optional[list[dict]] = None,
    ) -> None:
        self.meet_results[connection_id] = {
            "signal_id": signal_id,
            "routed_to": routed_to,
            "narrative": narrative,
            "gmail_draft": gmail_draft,
            "outreach_sequence": outreach_sequence,
            "interests": interests or [],
            "relations": relations or [],
            "knowledge_graph": knowledge_graph or [],
            "matched_candidates": matched_candidates or [],
            "recorded_at": datetime.utcnow().isoformat(),
        }
        self.signal_index[signal_id] = connection_id
        conn = self.get_connection(connection_id)
        if conn and interests:
            merged = list(dict.fromkeys([*conn.interests, *interests]))
            conn.interests = merged
            self.upsert_connection(conn)

    def meet_result_for(self, connection_id: str) -> Optional[dict[str, Any]]:
        return self.meet_results.get(connection_id)

    def record_knowledge_graph(
        self,
        connection_id: str,
        *,
        people: list[dict[str, Any]] | None = None,
        person: dict[str, Any] | None = None,
        narrative: str | None = None,
        signal: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "people": people or [],
            "person": person,
            "narrative": narrative,
            "signal": signal,
            "recorded_at": datetime.utcnow().isoformat(),
        }
        if person and not payload["people"]:
            payload["people"] = [person]
        self.knowledge_graphs[connection_id] = payload

    def knowledge_graph_for(self, connection_id: str) -> Optional[dict[str, Any]]:
        return self.knowledge_graphs.get(connection_id)

    def upsert_lead_from_signal(
        self,
        payload: Any,
        connection_id: str,
        summary: dict[str, Any],
    ) -> Lead:
        lead = Lead(
            company_name=(
                payload.company.name if getattr(payload, "company", None)
                else payload.person.company or "Unknown Company"
            ),
            contact_name=payload.person.name,
            icp_score=int(payload.icp_pre_score),
            signal_source="event_audio",
            tags=list(payload.person.icp_keywords_hit),
        )
        self.leads[lead.id] = lead
        return lead


import os  # noqa: E402

from .user_context import current_user_id  # noqa: E402

_user_stores: dict[str, DemoStore] = {}
_repo: "UserStoreRepository | None" = None


def _use_firestore_store() -> bool:
    return os.getenv("USE_FIRESTORE_STORE", "").lower() in ("1", "true", "yes")


def _get_repo():
    global _repo
    if _repo is not None:
        return _repo
    from ...infra.firebase.admin import ensure_firebase_initialized
    from ...infra.firebase.user_store_repo import UserStoreRepository
    from firebase_admin import firestore as firebase_firestore

    ensure_firebase_initialized()
    _repo = UserStoreRepository(firebase_firestore.client())
    return _repo


def _should_seed_demo_store() -> bool:
    """Seed hackathon roster only for local unauthenticated dev."""
    from ...packages.core.auth import auth_required  # noqa: WPS433

    if auth_required():
        return False
    return os.getenv("SEED_DEMO_DATA", "true").lower() in ("1", "true", "yes")


def get_store(user_id: str | None = None) -> DemoStore:
    """Return the store for the authenticated user (in-memory or Firestore-backed)."""
    uid = user_id or current_user_id.get()
    if uid not in _user_stores:
        seed = uid == DEMO_USER_ID and _should_seed_demo_store()
        if _use_firestore_store():
            from .persisted_store import FirestoreBackedStore

            _user_stores[uid] = FirestoreBackedStore(uid, _get_repo(), seed=seed)
        else:
            _user_stores[uid] = DemoStore(seed=seed)
    return _user_stores[uid]
