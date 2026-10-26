#!/usr/bin/env python3
"""DRAFT — NetworkX + temporal graph scoring prototype (does NOT touch prod code).

Explores the architecture described in warmth-ios-technical-architecture.md:
  - PersonKnowledgeGraph  →  NetworkX multi-relational graph (persistent draft)
  - WarmthModel stub      →  temporal window scorer (TGN-*inspired*, not a real TGN)
  - Community routing     →  spectral centrality + warm-intro path length (GCN-*inspired*)

This script is fully self-contained. It does not import warmth.* or call the API.

Optional dependency (install once, local only):
  uv pip install networkx

Run from repo root:
  uv run python scripts/draft/meet_graph_scoring_draft.py
  uv run python scripts/draft/meet_graph_scoring_draft.py --json
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    import networkx as nx
except ImportError as exc:  # pragma: no cover - draft convenience
    raise SystemExit(
        "Draft requires networkx. Install locally (does not change prod deps):\n"
        "  uv pip install networkx"
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
ATTENDEES_FILE = REPO_ROOT / "data" / "gtm_hackathon_attendees.json"

# ---------------------------------------------------------------------------
# Sample data — GTM Hackathon roster + a synthetic post-meet conversation
# ---------------------------------------------------------------------------

DEFAULT_ATTENDEES = [
    {
        "name": "Moly Leelatham",
        "email": "molyleelatham@gmail.com",
        "company": "CLARK",
        "interests": ["ai", "marketing", "founder"],
        "icp_score": 88,
        "predicted_warmth": 82,
    },
    {
        "name": "Nick Wong",
        "email": "nicholasyswong@googlemail.com",
        "company": "Imperial College London",
        "interests": ["gtm", "revops", "hubspot", "ai"],
        "icp_score": 76,
        "predicted_warmth": 68,
    },
]

# Diarized windows: speaker 0 = you, speaker 1 = counterpart (Nick Wong demo)
MEET_WINDOWS = [
    {"t": 0, "speaker": 0, "text": "Hey Nick, great to meet you at the hackathon."},
    {
        "t": 30,
        "speaker": 1,
        "text": (
            "Likewise — I'm really interested in GTM pipeline and HubSpot attribution. "
            "We need faster conference follow-up; manual CRM entry is painful."
        ),
    },
    {
        "t": 60,
        "speaker": 1,
        "text": (
            "RevOps is my focus. I'd love to swap notes on AI scoring and warm intros "
            "after events like this."
        ),
    },
]


# ---------------------------------------------------------------------------
# Draft graph builder (NetworkX) — NOT PersonKnowledgeGraph in prod
# ---------------------------------------------------------------------------


@dataclass
class TemporalWindow:
    """One ~30s slice of conversation (TGN-style event)."""

    start_sec: float
    end_sec: float
    speaker_id: int
    text: str
    topics: list[str] = field(default_factory=list)
    pains: list[str] = field(default_factory=list)
    engagement: float = 0.0


class KnowledgeGraphBuilderDraft:
    """Build a heterogeneous graph: people, topics, interests, companies."""

    TOPIC_KEYWORDS = (
        "gtm",
        "revops",
        "hubspot",
        "pipeline",
        "crm",
        "ai",
        "marketing",
        "saas",
        "sales",
        "conference",
        "attribution",
    )
    PAIN_CUES = ("painful", "pain", "frustrated", "hate", "manual", "slow", "hard")

    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()
        self.windows: list[TemporalWindow] = []
        self.session_id = f"draft_kg_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"

    def add_attendee(self, attendee: dict[str, Any]) -> str:
        email = attendee["email"].lower()
        node_id = f"person:{email}"
        self.graph.add_node(
            node_id,
            kind="person",
            name=attendee.get("name"),
            email=email,
            company=attendee.get("company"),
            icp_score=float(attendee.get("icp_score", 50)),
            predicted_warmth=float(attendee.get("predicted_warmth", 50)),
        )
        if attendee.get("company"):
            company_id = f"company:{attendee['company'].lower().replace(' ', '_')}"
            self.graph.add_node(company_id, kind="company", label=attendee["company"])
            self.graph.add_edge(node_id, company_id, rel="works_at", weight=1.0)

        for interest in attendee.get("interests") or []:
            iid = f"interest:{interest.lower()}"
            self.graph.add_node(iid, kind="interest", label=interest)
            self.graph.add_edge(node_id, iid, rel="interested_in", weight=1.0)
        return node_id

    def ingest_window(
        self,
        window: dict[str, Any],
        *,
        counterpart_node: Optional[str] = None,
    ) -> TemporalWindow:
        text = (window.get("text") or "").lower()
        start = float(window.get("t", 0))
        tw = TemporalWindow(
            start_sec=start,
            end_sec=start + 30.0,
            speaker_id=int(window.get("speaker", 0)),
            text=text,
            topics=[kw for kw in self.TOPIC_KEYWORDS if kw in text],
            pains=[cue for cue in self.PAIN_CUES if cue in text],
            engagement=min(1.0, len(text.split()) / 40.0),
        )
        self.windows.append(tw)

        if tw.speaker_id == 0:
            return tw

        person_nodes = [n for n, d in self.graph.nodes(data=True) if d.get("kind") == "person"]
        if not person_nodes:
            return tw
        target = counterpart_node if counterpart_node in self.graph else person_nodes[0]
        for topic in tw.topics:
            tid = f"topic:{topic}"
            self.graph.add_node(tid, kind="topic", label=topic)
            self.graph.add_edge(
                target,
                tid,
                rel="discussed",
                weight=tw.engagement,
                at_sec=tw.start_sec,
            )
        for pain in tw.pains:
            pid = f"pain:{pain}"
            self.graph.add_node(pid, kind="pain", label=pain)
            self.graph.add_edge(
                target,
                pid,
                rel="expressed_pain",
                weight=0.5 + tw.engagement * 0.5,
                at_sec=tw.start_sec,
            )
        return tw

    def link_attendees(self, a_email: str, b_email: str, *, context: str = "met_at_event") -> None:
        """Co-attendance edge (conference graph)."""
        a, b = f"person:{a_email.lower()}", f"person:{b_email.lower()}"
        if a in self.graph and b in self.graph:
            self.graph.add_edge(a, b, rel=context, weight=1.0)
            self.graph.add_edge(b, a, rel=context, weight=1.0)


# ---------------------------------------------------------------------------
# Draft scorers — heuristic stand-ins for TGN + spectral GCN
# ---------------------------------------------------------------------------


class TemporalGraphScorerDraft:
    """TGN-inspired: aggregate time-stamped edges into a post-meet warmth score."""

    def score(self, builder: KnowledgeGraphBuilderDraft, person_node: str) -> dict[str, Any]:
        topic_mass = 0.0
        pain_mass = 0.0
        for _, _, data in builder.graph.out_edges(person_node, data=True):
            rel = data.get("rel")
            w = float(data.get("weight", 0))
            if rel == "discussed":
                topic_mass += w
            elif rel == "expressed_pain":
                pain_mass += w

        window_engagement = sum(w.engagement for w in builder.windows if w.speaker_id != 0)
        recency_boost = 1.0 + 0.1 * len(builder.windows)

        warmth = min(
            100.0,
            20.0
            + topic_mass * 18.0
            + pain_mass * 12.0
            + window_engagement * 15.0 * recency_boost,
        )
        return {
            "temporal_warmth": round(warmth, 2),
            "topic_mass": round(topic_mass, 3),
            "pain_mass": round(pain_mass, 3),
            "window_count": len(builder.windows),
        }


class SpectralCentralityScorerDraft:
    """GCN-inspired proxy: spectral / PageRank centrality on the draft graph."""

    def score(self, builder: KnowledgeGraphBuilderDraft, person_node: str) -> dict[str, Any]:
        g = builder.graph
        if g.number_of_nodes() < 2:
            return {"centrality": 0.0, "warm_intro_hops": None}

        try:
            centrality = nx.eigenvector_centrality_numpy(g, weight="weight")
        except Exception:
            centrality = nx.degree_centrality(g)

        person_c = float(centrality.get(person_node, 0.0))

        # Warm-intro path: shortest path to other attendees via co-attendance edges
        hops: dict[str, int] = {}
        for node, data in g.nodes(data=True):
            if data.get("kind") != "person" or node == person_node:
                continue
            try:
                path = nx.shortest_path(
                    g.to_undirected(),
                    person_node,
                    node,
                    weight=None,
                )
                hops[data.get("name") or node] = max(0, len(path) - 1)
            except nx.NetworkXNoPath:
                hops[data.get("name") or node] = -1

        return {
            "centrality": round(person_c, 4),
            "warm_intro_hops": hops,
        }


class MeetRoutingDraft:
    """Combine draft scores → routing decision (mirrors prod uplift logic)."""

    def decide(
        self,
        *,
        icp_score: float,
        predicted_warmth: float,
        temporal_warmth: float,
        centrality: float,
        uplift_threshold: float = 0.0,
    ) -> dict[str, Any]:
        # Blend temporal warmth with graph centrality (draft only)
        actual = min(100.0, 0.75 * temporal_warmth + 0.25 * (centrality * 100.0))
        uplift = actual - predicted_warmth
        improved = uplift > uplift_threshold if predicted_warmth else actual >= 70.0

        return {
            "icp_score": icp_score,
            "predicted_warmth": predicted_warmth,
            "actual_warmth_draft": round(actual, 2),
            "uplift": round(uplift, 2),
            "route": "crm_and_outreach" if improved else "founder_community",
            "reason": (
                "Draft temporal+graph score exceeded pre-meet prediction."
                if improved
                else "Draft score flat; route to founder community for warm intro."
            ),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_attendees() -> list[dict[str, Any]]:
    if ATTENDEES_FILE.exists():
        raw = json.loads(ATTENDEES_FILE.read_text())
        out = []
        for row in raw:
            email = (row.get("email") or "").strip()
            if not email:
                continue
            out.append(
                {
                    "name": row.get("name"),
                    "email": email,
                    "company": row.get("company") or row.get("company_name"),
                    "interests": [i.lower() for i in (row.get("interests") or [])],
                    "icp_score": 75,
                    "predicted_warmth": 65,
                }
            )
        return out or DEFAULT_ATTENDEES
    return DEFAULT_ATTENDEES


def run_demo(*, counterpart_email: str, as_json: bool) -> dict[str, Any]:
    attendees = load_attendees()
    builder = KnowledgeGraphBuilderDraft()
    person_ids: dict[str, str] = {}
    for att in attendees:
        person_ids[att["email"].lower()] = builder.add_attendee(att)

    emails = list(person_ids.keys())
    for i, a in enumerate(emails):
        for b in emails[i + 1 :]:
            builder.link_attendees(a, b)

    counterpart = person_ids.get(counterpart_email.lower())
    if not counterpart:
        raise SystemExit(f"Unknown counterpart email: {counterpart_email}")

    for window in MEET_WINDOWS:
        builder.ingest_window(window, counterpart_node=counterpart)

    att = next(a for a in attendees if a["email"].lower() == counterpart_email.lower())
    temporal = TemporalGraphScorerDraft().score(builder, counterpart)
    spectral = SpectralCentralityScorerDraft().score(builder, counterpart)
    routing = MeetRoutingDraft().decide(
        icp_score=float(att.get("icp_score", 50)),
        predicted_warmth=float(att.get("predicted_warmth", 50)),
        temporal_warmth=temporal["temporal_warmth"],
        centrality=spectral["centrality"],
    )

    result = {
        "draft": True,
        "disclaimer": "Experimental — not used by MeetStageAgent or production API.",
        "session_id": builder.session_id,
        "graph": {
            "nodes": builder.graph.number_of_nodes(),
            "edges": builder.graph.number_of_edges(),
        },
        "counterpart": att["name"],
        "counterpart_email": counterpart_email,
        "temporal_scorer": temporal,
        "spectral_scorer": spectral,
        "routing": routing,
        "sample_windows": MEET_WINDOWS,
    }

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        _print_report(result)
    return result


def _print_report(result: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("  WARMTH DRAFT — NetworkX meet graph scoring (NOT PROD)")
    print("=" * 60)
    print(f"  Session:     {result['session_id']}")
    print(f"  Graph:       {result['graph']['nodes']} nodes, {result['graph']['edges']} edges")
    print(f"  Counterpart: {result['counterpart']} <{result['counterpart_email']}>")
    print()
    t = result["temporal_scorer"]
    print("  Temporal scorer (TGN-inspired draft):")
    print(f"    warmth:        {t['temporal_warmth']}")
    print(f"    topic mass:    {t['topic_mass']}")
    print(f"    pain mass:     {t['pain_mass']}")
    print(f"    windows:       {t['window_count']}")
    print()
    s = result["spectral_scorer"]
    print("  Spectral scorer (GCN-inspired draft):")
    print(f"    centrality:    {s['centrality']}")
    print(f"    warm-intro:    {s['warm_intro_hops']}")
    print()
    r = result["routing"]
    print("  Routing decision:")
    print(f"    predicted:     {r['predicted_warmth']}")
    print(f"    actual (draft): {r['actual_warmth_draft']}")
    print(f"    uplift:        {r['uplift']}")
    print(f"    route:         {r['route']}")
    print(f"    reason:        {r['reason']}")
    print("\n" + "=" * 60 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft meet graph scoring (isolated from prod).")
    parser.add_argument(
        "--counterpart",
        default="nicholasyswong@googlemail.com",
        help="Email of the person you 'met' in the synthetic transcript.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args()
    run_demo(counterpart_email=args.counterpart, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
