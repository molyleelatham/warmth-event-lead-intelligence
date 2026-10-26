#!/usr/bin/env python3
"""GTM Hackathon pull — calendar attendees only + Tavily (LinkedIn / Google).

Does NOT merge the investor CSV. Uses only invitees on the GTM Hackathon
London calendar event, enriches each via Tavily LinkedIn research, then runs
pre-meet drafts.

Usage:
  make run-gmail-mcp
  PYTHONPATH=. uv run python scripts/run_gtm_hackathon_pull.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
load_dotenv(REPO_ROOT / ".env")

from warmth.apps.api.integration_helpers import (  # noqa: E402
    gmail_client_optional,
    hubspot_client_optional,
    tavily_client_optional,
    unify_client_optional,
    zero_client_optional,
    warmth_client_email,
    warmth_client_name,
)
from warmth.apps.lifecycle.contact_sync import ContactSyncPipeline  # noqa: E402
from warmth.apps.lifecycle.premeet import PreMeetPipeline  # noqa: E402
from warmth.packages.core.models.event import DetectedEvent, EventType, LifecycleStage  # noqa: E402
from warmth.packages.integrations.google_calendar.attendees import calendar_attendees_from_raw  # noqa: E402
from warmth.packages.integrations.google_calendar.client import GoogleCalendarClient  # noqa: E402
from warmth.packages.integrations.tavily.client import TavilyClient  # noqa: E402
from warmth.packages.integrations.tavily.linkedin_enricher import LinkedInEnricher  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from run_premeet_e2e import _render_briefing  # noqa: E402

GTM_HINTS = ("gtm", "hackathon")
TOP_N = 10
GTM_NAME_OVERRIDES = {
    "molyleelatham@gmail.com": "Moly Leelatham",
    "nicholasyswong@googlemail.com": "Nick Wong",
}
GTM_PROFILE_HINTS = {
    "molyleelatham@gmail.com": {
        "linkedin": "https://uk.linkedin.com/in/moly-leelatham",
    },
    "nicholasyswong@googlemail.com": {
        "linkedin": "https://uk.linkedin.com/in/nicholasyswong",
    },
}


def _pick_gtm_event(events: list) -> tuple[Any, dict]:
    for ev in events:
        if any(h in (ev.title or "").lower() for h in GTM_HINTS):
            return ev, ev.raw or {}
    if not events:
        raise SystemExit("No calendar events found.")
    return events[0], events[0].raw or {}


async def run() -> dict[str, Any]:
    print("\n" + "=" * 60)
    print("  GTM HACKATHON PULL (calendar-only + Tavily LinkedIn)")
    print("=" * 60 + "\n")

    cal = GoogleCalendarClient()
    now = datetime.now(timezone.utc)
    events = await cal.list_events(
        time_min=(now - timedelta(days=30)).replace(tzinfo=None),
        time_max=(now + timedelta(days=60)).replace(tzinfo=None),
    )
    cal_ev, raw = _pick_gtm_event(events)

    warmth_inbox = warmth_client_email().lower()
    attendees = calendar_attendees_from_raw(
        raw,
        exclude_emails={warmth_inbox},
    )
    for attendee in attendees:
        override = GTM_NAME_OVERRIDES.get(attendee["email"].lower())
        if override:
            attendee["name"] = override

    print(f"[1/5] Calendar: {cal_ev.title}")
    print(f"      Location: {cal_ev.location}")
    print(f"      Invitees ({len(attendees)}):")
    for a in attendees:
        print(f"        • {a['name']} <{a['email']}>")

    print("[2/5] Tavily LinkedIn enrichment…")
    tavily = TavilyClient()
    enricher = LinkedInEnricher(tavily)
    enriched = await asyncio.gather(*[enricher.enrich_attendee(a) for a in attendees])
    for attendee in enriched:
        email = attendee["email"].lower()
        if email in GTM_NAME_OVERRIDES:
            attendee["name"] = GTM_NAME_OVERRIDES[email]
        hints = GTM_PROFILE_HINTS.get(email) or {}
        if hints.get("linkedin"):
            attendee["linkedin"] = hints["linkedin"]
    for a in enriched:
        print(
            f"        → {a['name']}: {a.get('linkedin') or 'no LinkedIn'} | "
            f"{a.get('industry') or 'unknown industry'} | "
            f"interests: {', '.join(a.get('interests') or [])[:60]}"
        )

    out_dir = REPO_ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "gtm_hackathon_attendees.json"
    out_path.write_text(json.dumps(enriched, indent=2))
    print(f"      Saved → {out_path}")

    event = DetectedEvent(
        id="event_gtm_hackathon_london",
        user_id="demo-user",
        calendar_event_id=cal_ev.external_id,
        name=cal_ev.title,
        event_type=EventType.EVENT,
        location=cal_ev.location,
        start_date=cal_ev.start_time,
        end_date=cal_ev.end_time,
        confidence=1.0,
        stage=LifecycleStage.BEFORE_MEET,
        attendee_count=len(enriched),
    )

    print("[3/5] Contact sync pipeline…")
    contact_sync = ContactSyncPipeline(
        hubspot_client=hubspot_client_optional(),
        unify_client=unify_client_optional(),
        zero_client=zero_client_optional(),
        tavily_client=tavily,
    )
    sync_result = await contact_sync.process_batch(
        attendees=enriched,
        event_id=event.id,
        event_name=event.name,
    )
    synced_connections = sync_result["connections"]
    hubspot_result = sync_result.get("hubspot", {})
    print(
        f"      Contact sync: {len(synced_connections)} attendees | "
        f"HubSpot {hubspot_result.get('created', 0)} created, "
        f"{hubspot_result.get('updated', 0)} updated"
    )

    print("[4/5] Pre-meet pipeline…")
    pipeline = PreMeetPipeline(
        unify_client=unify_client_optional(),
        zero_client=zero_client_optional(),
        gmail_client=gmail_client_optional(),
        tavily_client=tavily,
    )
    top = await pipeline.run(event, manual_attendees=enriched, top_n=min(TOP_N, len(enriched)))

    print("[5/5] Briefing draft…")
    gmail = gmail_client_optional()
    briefing_id = None
    if gmail:
        subj, body = _render_briefing(event, top, client_name=warmth_client_name())
        body += "\n\nATTENDEES (from calendar — not CSV)\n"
        for a in enriched:
            body += f"  • {a['name']} <{a['email']}>\n"
            if a.get("linkedin"):
                body += f"    LinkedIn: {a['linkedin']}\n"
            if a.get("industry"):
                body += f"    Industry: {a['industry']}\n"
            if a.get("interests"):
                body += f"    Interests: {', '.join(a['interests'])}\n"
        draft = await gmail.create_email_draft(to=warmth_client_email(), subject=subj, body=body)
        briefing_id = draft.get("id")

    print("\n" + "=" * 60)
    print("  Done")
    print("=" * 60 + "\n")

    from warmth.apps.api.store import store

    store.refresh_gtm_hackathon(
        enriched,
        premeet_results=top,
        sync_results=sync_result,
    )
    print("      Dashboard store updated")

    return {
        "event": event.name,
        "attendees": enriched,
        "top_leads": [c.model_dump() for c in top],
        "briefing_draft_id": briefing_id,
        "hubspot": hubspot_result,
        "file": str(out_path),
    }


def main() -> int:
    result = asyncio.run(run())
    print(json.dumps(
        {
            "event": result["event"],
            "attendee_count": len(result["attendees"]),
            "names": [a["name"] for a in result["attendees"]],
            "briefing_draft_id": result["briefing_draft_id"],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
