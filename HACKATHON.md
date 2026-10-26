# Warmth at the GTM Hackathon

**Result: 2nd place.**

This is the hackathon write-up. For a guide to the codebase, see [README.md](README.md).

## The event

Warmth was built for the GTM Hackathon on 20 June 2026. The seeded event in the app is GTM Hackathon London, at The Building Centre, running that day from 08:30 to 18:00 (`apps/api/store.py`).

Commit timestamps are British Summer Time (UTC+1). The first commit is 11:45 on 20 June. Work continues until 01:06 on 21 June. A follow-up commit at 12:00 on 22 June adds Tavily LinkedIn enrichment and the Cloud Run deploy script. The history does not record when judging happened, so it is not clear which of the late-evening commits were in the judged build.

| When (UK) | What landed |
|---|---|
| 20 June, 11:45–12:53 | Initial platform. iPhone and Watch app structure. Porcupine wake word removed and replaced with a phrase trigger ("hey it's nice to meet you"). Firebase and GCP project setup. |
| 13:56–16:24 | Lifecycle pipeline, web dashboard and Google sign-in. On-device listening pipeline and secrets tooling. Xcode app with the Watch companion. End-to-end meet pipeline and Gmail bridge. HubSpot and Zero CRM contact schema merge. |
| 16:33–18:51 | Dashboard redesign. HubSpot sync and attendee matching. Dashboard connected to the live API. Demo artefacts and the submission notes. |
| 18:56–23:37 | iPhone pointed at the live backend. Local notifications for matched attendees. "Conference" renamed "event". Event mode and settings on the iPhone. Landing page. API auth hardening and rate limiting. |
| 21 June, 00:05–01:06 | Firestore persistence for dashboard data. Production auth fixes. |
| 22 June, 12:00 | Afterwards: Tavily LinkedIn enrichment, the Cloud Run deploy script for `warmth-api` in `warmth-gtm-hackathon`, and roster fixes. |

## What we submitted

Warmth was a way to turn an event conversation into a scored lead and a follow-up draft while you were still on the floor. Three surfaces share one backend:

| Part | Role | When it's used |
|---|---|---|
| iPhone, plus a Watch companion in the Xcode project | Capture: wake phrase or name, live transcription, on-device NLP | On the event floor |
| Web dashboard | Review: events, warmth scores, leads, follow-ups | Before and after the event |
| Python backend | Scoring, CRM routing, follow-up drafts | Always on |

The current iPhone build listens for contact and ICP names through the Soniqo wake-word engine and opens a 30-second on-device transcription window. On the morning of 20 June the project still used Porcupine, which was removed the same morning and replaced with a spoken phrase ("hey it's nice to meet you"). `POST /api/signals` still accepts that older `EventAudioSignal` shape alongside the current `CapturedSignal`. The Watch app and complication are targets in `iOS/Warmth-iOS/project.yml`; the iPhone target does not embed them yet.

### The lifecycle

1. **Onboarding.** Connect Google Calendar and Gmail through the Google MCP bridge (`apps/lifecycle/onboarding.py`).
2. **Before the meeting** (`premeet.py`). Build an attendee list from calendar invites and, when a directory URL is set, from the optional Playwright scraper. Enrich with UnifyGTM and Tavily. Score ICP fit and a pre-meet warmth prediction. Draft outreach.
3. **The meeting.** A wake word or the older phrase starts capture. The phone transcribes with `SFSpeechRecognizer`. `SocialGraphEngine` extracts people, organisations and relationships. A `CapturedSignal` is posted to the backend, which runs `MeetStageAgent` and the meet pipeline.
4. **After the meeting.** A Gmail draft is created for the inbox in `WARMTH_CLIENT_EMAIL` (the default in `.env.example` is `getwarmth@gmail.com`). The draft carries the ICP and warmth scores, the lead details, and a short narrative about the person. Lightfern is the path that polishes that draft; a person sends it.

### Per-person context

This is the part of the pipeline with the most structure behind it. `MeetEncoder` walks a diarised transcript in windows of about 30 seconds. Each window can produce a `PersonalContext`:

- communication style
- values
- topic weights
- learnings
- pain points, each with an intensity
- a transcript excerpt

`PersonNode.update()` folds those windows together: it merges style and values, sums and renormalises topic weights, and escalates pain intensity. `to_narrative()` turns the node into the short summary stored with the CRM record.

Extraction is heuristic by default (`PersonContextBuilder`). Set `WARMTH_USE_AGENT=true`, or pass `use_agent: true` to the meet endpoints, and `AgentContextExtractor` fills the same fields through the Cursor SDK. If the SDK is missing or the call fails, the heuristic result is what remains.

### Scoring and routing

ICP fit and warmth are separate scores. The warmth model docstring treats ICP fit as coming from Zero CRM, with a local fallback in the pre-meet pipeline, and layers intent, engagement and relationship on top. The weights in `packages/ml/warmth_model.py` are a placeholder (`model_version="stub-v0"`):

- ICP = 0.7 × fit + 0.3 × topic relevance
- warmth = 0.4 × intent + 0.35 × engagement + 0.25 × relationship
- the combined score blends the average with the product
- bands on that combined score: hot ≥ 70, warm ≥ 40, otherwise cold

`MeetIntelligencePipeline` compares the post-meet score with the pre-meet prediction. Uplift above the threshold (default 0) routes to CRM and outreach, which is where Zero, Faxxing and the Gmail draft come in. A flat or lower score routes to the founder community, using the clusterer's nearest match. A second classifier, `LeadClassifier`, can send a lead to you, your team, the founders or the community from keywords in the transcript. Neither path is a trained model.

### API the demo used

| Endpoint | Stage |
|---|---|
| `POST /api/signals` | iPhone ingress, then scoring and a Gmail draft |
| `POST /api/v1/meet/encode` | Diarised transcript to a `MeetingSignal` and knowledge graph |
| `POST /api/v1/meet/process` | Encode, score and Gmail handoff in one call |
| `GET /api/v1/dashboard` | What the web dashboard reads |
| `POST /api/v1/events/{event_id}/premeet` | Before-meet pipeline |

The in-memory store (`apps/api/store.py`) seeds a sample event so the API and dashboard can run without Firebase. With `USE_FIRESTORE_STORE=true`, the same roster can be written under `users/{uid}/…` for the account in `WARMTH_DEMO_USER_EMAIL`.

### Web dashboard

React, Vite and Tailwind, with the same warm glass look as the iPhone app: frosted cards, Space Grotesk, and a red-to-orange heat ramp for ICP. The interest graph built in `apps/api/graph_builder.py` connects a person to interests, topics, values and pain points on the connection view.

### Integrations

| Integration | What the code uses it for |
|---|---|
| Zero CRM | ICP fit, contact lookup, lead push. The warmth model keeps a local fallback. |
| UnifyGTM | Firmographics and related enrichment on the pre-meet path. |
| Google MCP | Calendar reads and Gmail drafts. |
| Lightfern | Polish for the Gmail draft. |
| Faxxing | Outreach sequence on the CRM routing path. |
| HubSpot | Contact schema shared with Zero, and source-of-record upsert. |
| Tavily | Search, and LinkedIn enrichment added on 22 June. |
| Cursor SDK | Optional `AgentContextExtractor`. |
| Firebase | Auth, Firestore, Hosting. |
| Deepgram | Optional backend ASR client. The iPhone path uses `SFSpeechRecognizer`. |
| Soniqo | Current on-device wake word. Porcupine was removed on 20 June. |

## Known limits

- Lead and warmth scores are hand-set heuristics (`stub-v0`).
- The iPhone project has XCTest sources, and CI runs the Python unit tests. The web dashboard has no test suite.
- Cursor SDK and Tavily integration tests need their own API keys, so CI does not run them.
- Directory scraping depends on Playwright, which is not installed by `uv sync`.
