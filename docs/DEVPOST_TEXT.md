**Project name:** CareCircle — Alexa+ for families who care from far away

**Tagline:** A self-hosted MCP server that lets Alexa+ refuse a double dose, notice a missed one on its own, and tell the family when a rule is bent.

**Track:** Alexa+ (self-hosted MCP server, spec 2025-11-25, Streamable HTTP; web simulator for the Echo screen)
**Mini challenges:** Open Source (new MIT project: `mcp-voice-check`, a voice-readiness checker for any MCP server) · AWS Builder (Amazon SNS alert delivery + container for App Runner)

## Inspiration
A pill taken twice makes no noise. Neither does a pill that was never taken, or a lonely week nobody hears about. For an older adult who lives alone and a daughter two hours away, those quiet failures are the ones that hurt. Alexa+ is already in the room, so we asked what it would take to give it this one job — safely.

## What it does
CareCircle is an MCP server Alexa+ connects to. Mrs. Han just talks; the server keeps the rules.
* "What do I need to do today?" → `get_daily_plan` reads out what is due.
* "I took my blood pressure pill" → `confirm_medication` logs it. Said again a few minutes later, the tool **refuses** to log a second dose and tells her not to take another. A dose that is not due yet is not logged either.
* "My doctor told me to take my evening metformin now" → the agent may pass `force`; the dose is logged, and the server **always** sends the family an *important* alert about the override. A safety rule can bend, but never silently.
* **Nobody says anything** → a server-side watcher notices a dose still unlogged two hours after its time, marks it missed and alerts Jisoo once per missed dose. No one has to speak first.
* "I feel lonely and tired today" → `check_in` saves it; three low check-ins in a row send the family an *important* alert.
* "I fell in the kitchen" → `notify_family` escalates to **urgent** automatically and alerts both children, while Alexa tells her to call emergency services.
* "How have I been this week?" → `get_family_digest` sums up adherence, missed doses and the mood trend in one sentence, the same digest the family dashboard shows.

## How we built it
* **MCP server** (Python, official SDK): 7 tools with described inputs, **typed output schemas** (every result has a required `spoken` field; refusal statuses such as `already_taken` / `too_early` are enumerated) and accurate read-only / destructive / open-world hints, 2 resources, 1 prompt and server instructions. Streamable HTTP at `/mcp`, protocol 2025-11-25 verified in tests.
* **Safety lives in the server, not in the model's judgment:** per-medicine minimum gaps, timing windows, a proactive missed-dose watcher, an alert ladder (info → important → urgent), automatic escalation for falls, chest pain, bleeding, breathing trouble and pain of 7/10 or more, and a family alert whenever a doctor-directed override is used.
* **Ready for a remote client:** with `CARECIRCLE_DEMO=0` (the container default) only `/mcp`, `/healthz` and the auth metadata are served and the Host header is checked; a bearer token protects `/mcp`; a `401` carries an RFC 9728 `resource_metadata` challenge and `/.well-known/oauth-protected-resource` points to your authorization server; a `Dockerfile` runs it on AWS App Runner, ECS or any HTTPS host.
* **Alexa+ simulator** (Echo Show screen + live MCP traffic inspector): we have no Alexa+ Preview access, so a small rule-based router stands in for the Alexa+ agent and fills the tool arguments. Every call after that goes through a real MCP client session over Streamable HTTP. A family-phone dashboard polls the same database.
* **Amazon SNS** (AWS Builder): each alert is published to a topic with an `urgency` message attribute, so the family can subscribe by SMS, email or push, and an urgent-only filter can drive a phone-call Lambda (not included). Verified with botocore's Stubber; not run against a live AWS account.
* **Tests:** 34 end-to-end checks with the official MCP client (output schemas, guards, the override alert, the watcher, auth metadata, public-mode lockdown, bearer token) plus SNS tests.

## Open Source mini challenge: `mcp-voice-check`
A separate MIT project that came out of this build: one command connects to any Streamable HTTP MCP server and reports what a voice agent depends on — protocol version, tool annotations, a real output schema per tool, a required speakable field, input descriptions and `tools/list` latency. It exits non-zero on failures, so it fits in CI. Our own first version passed all its tests yet published empty output schemas; `mcp-voice-check` caught all 7 in one run. Repository: https://github.com/thek-piano-cloud/mcp-voice-check

## Challenges we ran into
No Alexa+ Preview access, so we built to the public spec, simulated the agent, and say so in the README and the video. Most of the design time went into safety behavior: what to refuse, what to escalate, when to speak up unasked, and how to phrase each of those aloud.

## Accomplishments that we're proud of
Our proudest feature is what CareCircle refuses to do. Every refusal, override alert, missed-dose alert and escalation is covered by 34 automated end-to-end checks, run with the official MCP client against the live server over Streamable HTTP.

## What we learned
For a voice agent the tool result is part of the user interface. A typed result with a speakable line, plus rules the server enforces, is safer than any prompt.

## What's next
Speaking reminders on the Echo through Alexa+ proactive notifications, a live App Runner deployment with Login with Amazon, refill tracking, several residents per household, and multi-language voice.

**Built with:** Python, MCP (Model Context Protocol) SDK, Pydantic, Streamable HTTP, Starlette/Uvicorn, SQLite, Amazon SNS, boto3, Docker, HTML/CSS/JS, Playwright, FFmpeg

**Existed before the hackathon?** No — CareCircle and mcp-voice-check were designed and written during the submission window.

**Repository:** https://github.com/thek-piano-cloud/carecircle-alexa-mcp   **Open Source project:** https://github.com/thek-piano-cloud/mcp-voice-check   **Demo video (YouTube, public, English):** <VIDEO_URL>
