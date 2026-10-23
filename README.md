# CareCircle — Alexa+ for families who care from far away

> A self-hosted **MCP server** (spec **2025-11-25**, **Streamable HTTP**) that gives Alexa+ a safe, caring job:
> keeping an older adult's medicines on track, listening to how they feel, and keeping the family in the loop.
> Built for the **Alexa+ track** of *Build, Ship, Shape: Amazon Developer Hackathon*.

A pill taken twice makes no noise. Neither does a pill that was never taken, or a lonely week nobody hears about.
CareCircle gives Alexa+ the job of catching all three for an older adult who lives alone: it **refuses** a double
dose, **notices on its own** when a dose is two hours late, and **tells the family** when a rule is bent or a bad
day turns into a bad week. The safety rules live in the server, so they hold whatever the agent decides.

## What it does

| Moment | What Mrs. Han says | What happens (MCP tool) |
|---|---|---|
| Morning | "What do I need to do today?" | `get_daily_plan` → three medicines are due, next appointment |
| Taking a pill | "I took my blood pressure pill" | `confirm_medication` → dose logged, family dashboard updates |
| Confusion | says it again a few minutes later | `confirm_medication` **refuses** (double-dose guard) and tells her not to take another |
| Doctor's order | "My doctor told me to take my evening metformin now" | `confirm_medication` with `force` → logged, **and the family always gets an important alert about the override** |
| Nobody says anything | — (the dose is two hours late) | **watcher** marks it missed and sends Jisoo one **important** alert per missed dose |
| Low mood | "I feel lonely and tired today" | `check_in` → saved; *three low check-ins in a row* raise an **important** alert |
| Emergency | "I fell in the kitchen" | `notify_family` → escalated to **urgent**, both children alerted; she is told to call emergency services |
| Weekly | "How have I been this week?" (or Jisoo on the dashboard) | `get_family_digest` → adherence, missed doses, mood trend in one sentence |

The care rules live **in the server, not in the model's judgment**. Whatever agent calls the tools:
without a doctor-directed override, a repeat or early dose is never logged; with one, the family is always told.
A fall, chest pain, bleeding, trouble breathing or pain of 7/10 or more always becomes an urgent alert, and a dose
nobody mentions is noticed by the server itself.

## MCP surface

* **Transport:** Streamable HTTP at `/mcp` (stateless, JSON responses), protocol `2025-11-25` negotiated and verified in tests.
* **Tools (7):** `get_daily_plan`, `confirm_medication`, `skip_medication`, `check_in`, `notify_family`,
  `add_reminder`, `get_family_digest` — each with described, validated arguments, a **typed output schema**
  (Pydantic result models; `spoken` is required in every result and statuses such as `already_taken` / `too_early`
  are enumerated) and accurate `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` annotations, so
  the voice agent can branch on the status and read safety-critical text verbatim.
* **Resources (2):** `carecircle://care-plan`, `carecircle://alerts`.
* **Prompt (1):** `evening_check_in` — a script for a warm end-of-day conversation.
* **Server instructions:** tell the agent to prefer `spoken`, never to override a safety refusal, and to escalate emergencies.

## Run it (one command)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows (Git Bash): source .venv/Scripts/activate
pip install -r requirements.txt                       # requirements-dev.txt adds the video tooling
CARECIRCLE_DEMO_NOW=2026-10-23T08:05 python -m carecircle    # http://127.0.0.1:8780
```

* `http://127.0.0.1:8780/` — **Alexa+ simulator** (Echo Show screen + live MCP traffic inspector + demo clock)
* `http://127.0.0.1:8780/family` — **family dashboard** (Jisoo's phone)
* `http://127.0.0.1:8780/demo` — both side by side (what the demo video shows)
* `http://127.0.0.1:8780/mcp` — the **MCP endpoint** any MCP client can connect to

Without `CARECIRCLE_DEMO_NOW` the clock is the real wall clock. Data lives in `carecircle.db` (SQLite, seeded with one realistic past week).

### Connect a real MCP client

```json
{ "mcpServers": { "carecircle": { "type": "http", "url": "http://127.0.0.1:8780/mcp" } } }
```

(see `alexa_plus_mcp.json`). Anything that speaks Streamable HTTP works, e.g. `npx @modelcontextprotocol/inspector`.
To audit the server the way a voice agent sees it, run our companion project
[`mcp-voice-check`](https://github.com/thek-piano-cloud/mcp-voice-check): `python -m mcp_voice_check http://127.0.0.1:8780/mcp` → `0 failures`.

### Make it reachable for Alexa+ (HTTPS)

Alexa+ connects to remote MCP servers over HTTPS. The repo ships what that needs:

* **Container:** `Dockerfile` (listens on 8080, SQLite on `/data`) for AWS App Runner, ECS/Fargate or any container host,
  or run it on a home server behind any HTTPS tunnel/reverse proxy.
* **Public mode:** with `CARECIRCLE_DEMO=0` (the container default; on by default only when bound to localhost)
  the server answers only `/mcp`, `/healthz` and `/.well-known/*` — no demo pages, no `/api`. Set
  `CARECIRCLE_ALLOWED_HOSTS=care.example.com` to reject requests for any other Host (DNS-rebinding protection).
* **Auth:** set `CARECIRCLE_TOKEN`; a request without the token gets `401` with
  `WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource"`.
* **Discovery:** `/.well-known/oauth-protected-resource` serves RFC 9728 metadata. Set `CARECIRCLE_PUBLIC_URL`
  (your HTTPS origin) and `CARECIRCLE_AUTH_SERVERS` (comma-separated issuer URLs, e.g. Login with Amazon or Cognito).

For this submission the server ran locally; it has not been deployed to a public AWS endpoint.

## About the simulator (please read)

Alexa+ MCP integrations are in preview, so the Echo Show screen here is a **simulator**. On a real device Alexa+ is
the agent; in the simulator a small, transparent **intent router** (`carecircle/agent.py`, plain rules) plays that role
and fills the tool arguments the way an agent would (e.g. `"medication": "blood pressure pill"`).
Everything after the routing is real: for every utterance the simulator opens an **official MCP client session over
Streamable HTTP**, runs `initialize`, calls the chosen tool and speaks the tool's `spoken` result — you can watch each
`tools/call` in the inspector panel. The router never touches the care logic directly, and `tests/test_mcp_e2e.py`
proves the server with the official client independent of the simulator.

## Voice design notes

* **Short, kind, one idea per sentence** — every tool returns a `spoken` line written to be heard, not read.
* **Refuse with a reason and a next step** — "Hold on. You already took your Amlodipine at 8:05 AM. Please don't take another one."
* **Read-back for every write** — dose, time and (for reminders) day are repeated so the resident can correct a mistake.
* **Never leave her alone with bad news** — a low mood or a fall ends with who has been told, not just a statement of fact.
* **No jargon, no dosage advice** — CareCircle tracks the plan the doctor set; it does not prescribe, adjust or diagnose.

## Why it can serve people beyond a hackathon

* **No new hardware** — it is designed for the Echo the family already owns; the family side is any phone that can receive SMS or push.
* **Cheap to run** — a single small process with a SQLite file (a home server or a small VM); alerts cost one SNS publish each.
* **Private by construction** — medicine data stays on the family's own server; only first names appear in alerts.
* **Extensible** — the data model is already per-medicine and slot based; several residents per household or a care agency's
  caseload are additional rows, not a redesign. The same tools suit a caregiver's own assistant ("what did Mom miss this week?").

## Safety design

* **Double-dose guard** — per-medicine minimum gap; a repeat is refused and *not* logged.
* **Timing check** — a dose that is not due yet is not logged either.
* **Overrides are never silent** — `force` (meant for "my doctor told me to") can bypass either guard, but the family
  always gets an important alert saying which rule was bypassed.
* **Proactive watcher** — every minute the server checks today's doses; one still unlogged two hours after its
  time is marked missed and the family gets one important alert. Nobody has to speak first. (`CARECIRCLE_WATCH_SECONDS`, 0 turns it off.)
* **Alert ladder** — `info` (app) → `important` (push + SMS) → `urgent` (push + SMS through SNS subscriptions, plus a phone call via your own Lambda). A 7+/10 pain report, a fall, chest pain, bleeding or trouble breathing is urgent automatically;
  three low check-ins in a row are important.
* **Never claims what it cannot do** — Alexa tells the resident to call emergency services herself; CareCircle alerts the family, it does not pretend to be a medical alarm.
* **Small data** — one local SQLite file, no cloud account required, no personal identifiers beyond first names.

## Real delivery with Amazon SNS (optional, AWS)

Set `CARECIRCLE_SNS_TOPIC_ARN` and standard AWS credentials; every alert is also published to the topic with an
`urgency` message attribute. The family subscribes by SMS, email or mobile push, and an `urgent` subscription filter
(`{"urgency": ["urgent"]}`) can trigger a Lambda that places a phone call (the Lambda itself is not included). Without
SNS, alerts are recorded in the outbox the dashboard shows. A failed publish is reported in the tool
output and never breaks the care flow. `tests/test_sns.py` verifies the publish call and the failure path against
botocore's `Stubber`. *(Verified with the stub only; it has not been run against a live AWS account.)*

## Tests

```bash
python tests/test_mcp_e2e.py   # starts the real server, official MCP client: 34 checks (protocol 2025-11-25, typed output schemas, guards, override alert, escalation, proactive watcher, RFC 9728 metadata, public-mode lockdown, simulator path, bearer-token auth)
python tests/test_sns.py       # SNS delivery with a stubbed boto3 client
```

## Project layout

```
carecircle/mcp_server.py   FastMCP server: tools, resources, prompt, Streamable HTTP + demo routes
carecircle/service.py      care logic (plan, medication safety, check-ins, escalation, digest) over SQLite
carecircle/notifier.py     optional Amazon SNS delivery
carecircle/agent.py        Alexa+ simulator brain (intent router -> MCP client)
carecircle/web/            simulator, family dashboard, side-by-side demo page
tests/                     end-to-end (34 checks) and SNS tests
Dockerfile                 container for App Runner / ECS / any host
tools/make_video.py        how the demo video was produced from the running app
docs/                      product feedback + friction log, Devpost text
```

## Limitations and next steps

* The simulator's intent router is rule based; a real Alexa+ agent chooses tools itself from the tool descriptions.
* One resident and a fixed family circle in this version; the data model is ready for several residents.
* Authentication: `CARECIRCLE_TOKEN` gives a static bearer token (constant-time check, tested) and the RFC 9728 metadata
  points clients at your authorization server; issuing OAuth tokens is left to that server. The demo UI and `/api` exist only in demo mode (localhost by default).
* The watcher alerts the family; speaking a reminder on the Echo itself needs Alexa+ proactive notifications and is next.
* Next: caregiver-side MCP prompts ("what should I ask Mom tonight?"), refill tracking, multi-language voice.

MIT licensed — see `LICENSE`.
