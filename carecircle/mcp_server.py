"""CareCircle MCP server — Streamable HTTP, MCP spec 2025-11-25.

Alexa+ (or any MCP client) connects to ``/mcp``.  The same process also serves the
Alexa+ simulator and the family dashboard under ``/`` so the project can be
demonstrated without a physical Echo.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from . import clock, service

WEB = Path(__file__).resolve().parent / "web"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("CARECIRCLE_ALLOWED_HOSTS", "").split(",") if h.strip()]

mcp = FastMCP(
    "CareCircle",
    instructions=(
        "CareCircle helps an older adult living at home (the 'resident') stay on top of medicines, "
        "daily check-ins and family contact through voice. Prefer the `spoken` field of any result "
        "when answering aloud: it is short and already phrased for speech. Never override a safety "
        "refusal from confirm_medication (status already_taken / too_early) unless the resident or a "
        "caregiver explicitly says a doctor told them to. For anything that sounds like an emergency, "
        "call notify_family with urgency 'urgent' and tell the resident to call emergency services."
    ),
    host=os.environ.get("CARECIRCLE_HOST", "127.0.0.1"),
    port=int(os.environ.get("CARECIRCLE_PORT", "8780")),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    # CARECIRCLE_ALLOWED_HOSTS="care.example.com" turns on Host/Origin checks for a public deployment
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(ALLOWED_HOSTS), allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=[f"https://{h}" for h in ALLOWED_HOSTS]),
)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
NOTIFY = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


# --------------------------------------------------------------------------
# Output schemas: every result carries `spoken`; fields the agent must branch on are typed.
# Extra detail fields stay allowed so the family dashboard can reuse the same results.
# --------------------------------------------------------------------------

SPOKEN = Field(description="One or two short sentences already phrased for speech. Read this aloud verbatim, "
                           "especially for refusals and alerts.")


class Result(BaseModel):
    model_config = ConfigDict(extra="allow")
    spoken: str = SPOKEN


class Alert(BaseModel):
    model_config = ConfigDict(extra="allow")
    urgency: Literal["info", "important", "urgent"]
    message: str
    recipients: list[str]
    channel: str = Field(description="Delivery plan for this urgency level.")


class PlanResult(Result):
    date: str
    now: str
    medications: list[dict[str, Any]] = Field(description="One entry per medicine slot with state taken/due/upcoming/missed.")
    appointments: list[dict[str, Any]]
    reminders: list[dict[str, Any]]


class DoseResult(Result):
    status: Literal["logged", "already_taken", "too_early", "nothing_due", "needs_clarification", "unknown_medication"]
    logged: bool = Field(description="False means nothing was written: a safety refusal or a question back.")
    medication: str | None = None
    options: list[str] | None = Field(None, description="Candidates to ask about when status is needs_clarification.")
    override: bool = Field(False, description="True when a doctor-directed dose overrode a guard (family alerted).")
    family_alert: Alert | None = None


class SkipResult(Result):
    status: Literal["skipped", "nothing_due", "needs_clarification"]
    logged: bool
    medication: str | None = None
    family_alert: Alert | None = None


class CheckInResult(Result):
    status: Literal["saved"]
    mood: int
    pain: int
    alerts: list[Alert]


class NotifyResult(Result):
    status: Literal["sent"]
    notification: Alert


class ReminderResult(Result):
    status: Literal["saved", "bad_time"]
    text: str | None = None
    due: str | None = None


class DigestResult(Result):
    days: int
    adherence_percent: int
    doses_taken: int
    doses_missed: int
    mood_average: float | None
    mood_trend: Literal["declining", "steady", "improving"]
    headline: str


@mcp.tool(annotations=READ, title="Today's care plan")
def get_daily_plan() -> PlanResult:
    """What the resident has to do today: each medicine slot with its state (taken, due, upcoming,
    missed), the next appointments and open reminders. Use for "what do I need to do today?",
    "did I take my pills?" or "what's next?"."""
    return PlanResult(**service.daily_plan())


@mcp.tool(annotations=WRITE, title="Confirm a medicine was taken")
def confirm_medication(
    medication: Annotated[str, Field(description="Medicine name or what it is for, as the resident said it, "
                                                 "e.g. 'my blood pressure pill', 'metformin', 'the vitamin'.")],
    force: Annotated[bool, Field(description="Only true when the resident says a doctor told them to take it "
                                             "outside the usual schedule. Bypasses the timing guard, but the "
                                             "family is always alerted when it does.")] = False,
) -> DoseResult:
    """Record that the resident just took a medicine, after a safety check. Refuses (logged=false) when
    the same medicine was taken inside its minimum gap (double-dose guard) or when the dose is not due
    yet. Ask a short follow-up when status is 'needs_clarification'."""
    return DoseResult(**service.confirm_medication(medication, force))


@mcp.tool(annotations=WRITE, title="Skip a medicine")
def skip_medication(
    medication: Annotated[str, Field(description="Which medicine is being skipped.")],
    reason: Annotated[str, Field(description="Why, in the resident's words (nausea, dizziness, ran out...).")] = "",
) -> SkipResult:
    """Record that the resident is deliberately skipping a dose. After repeated skips or misses the
    family is notified automatically."""
    return SkipResult(**service.skip_medication(medication, reason))


@mcp.tool(annotations=WRITE, title="Daily wellbeing check-in")
def check_in(
    mood: Annotated[int, Field(ge=1, le=5, description="1 very low, 2 low, 3 so-so, 4 good, 5 great.")],
    pain: Annotated[int, Field(ge=0, le=10, description="Pain level 0-10, 0 if none mentioned.")] = 0,
    note: Annotated[str, Field(description="What the resident said, briefly.")] = "",
) -> CheckInResult:
    """Save how the resident feels. Strong pain (7+) alerts the family urgently; a streak of low moods
    raises an important alert; a single low mood sends a gentle heads-up."""
    return CheckInResult(**service.check_in(mood, pain, note))


@mcp.tool(annotations=NOTIFY, title="Message the family")
def notify_family(
    message: Annotated[str, Field(description="Message for the family, written in third person about the resident.")],
    urgency: Annotated[Literal["info", "important", "urgent"], Field(
        description="info = app notification, important = push + SMS, urgent = push + SMS + phone call "
                    "(delivered through Amazon SNS subscriptions when configured).")] = "info",
) -> NotifyResult:
    """Send a message to the resident's family circle. Wording that mentions a fall, chest pain, trouble
    breathing, bleeding or unconsciousness is automatically escalated to urgent."""
    return NotifyResult(**service.notify_family(message, urgency))


@mcp.tool(annotations=WRITE, title="Add a reminder")
def add_reminder(
    text: Annotated[str, Field(description="What to remind about, e.g. 'water the plants'.")],
    when: Annotated[str, Field(description="24h clock time like '15:00' (today, or tomorrow if past) or an ISO datetime.")],
) -> ReminderResult:
    """Create a one-off reminder for the resident, e.g. "remind me to call the pharmacy at three". Ask for
    a time when none was said (status bad_time)."""
    return ReminderResult(**service.add_reminder(text, when))


@mcp.tool(annotations=READ, title="Family digest")
def get_family_digest(
    days: Annotated[int, Field(ge=1, le=30, description="How many days to summarise.")] = 7,
) -> DigestResult:
    """Medicine adherence, missed doses, mood trend and recent alerts for the family. Use for "how was
    Mom's week?" or the resident's own "how have I been this week?"."""
    return DigestResult(**service.family_digest(days))


@mcp.resource("carecircle://care-plan", title="Care plan", mime_type="application/json")
def care_plan_resource() -> str:
    """The resident's medicines, schedule slots and dosing gaps."""
    return json.dumps({"resident": "Mrs. Han", "medications": service._meds()}, indent=2)


@mcp.resource("carecircle://alerts", title="Recent family alerts", mime_type="application/json")
def alerts_resource() -> str:
    """Latest messages sent to the family."""
    return json.dumps(service.outbox(20), indent=2)


@mcp.prompt(title="Evening check-in")
def evening_check_in() -> str:
    """Script for a warm end-of-day conversation."""
    return (
        "You are a gentle evening companion for Mrs. Han. 1) Call get_daily_plan and mention any unlogged "
        "medicine. 2) Ask how she feels today and about any pain; call check_in. 3) If she mentions "
        "anything worrying, call notify_family. Keep sentences short and warm."
    )


# --------------------------------------------------------------------------
# Demo surface: simulator + family dashboard + small JSON API
# --------------------------------------------------------------------------

def _page(name: str):
    async def handler(request: Request):
        return FileResponse(WEB / name, media_type="text/html")
    return handler


mcp.custom_route("/", methods=["GET"])(_page("simulator.html"))
mcp.custom_route("/family", methods=["GET"])(_page("family.html"))
mcp.custom_route("/demo", methods=["GET"])(_page("demo.html"))


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request):
    return JSONResponse({"ok": True, "service": "carecircle", "mcp": "/mcp", "protocol": "2025-11-25",
                         "clock": clock.now().isoformat(), "demo_clock": clock.is_demo()})


@mcp.custom_route("/api/state", methods=["GET"])
async def api_state(request: Request):
    return JSONResponse({"clock": clock.now().isoformat(), "plan": service.daily_plan(),
                         "digest": service.family_digest(7), "outbox": service.outbox(12)})


@mcp.custom_route("/api/utterance", methods=["POST"])
async def api_utterance(request: Request):
    from .agent import handle_utterance
    body = await request.json()
    return JSONResponse(await handle_utterance(str(body.get("text", "")), f"http://127.0.0.1:{mcp.settings.port}/mcp"))


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
@mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
async def protected_resource(request: Request):
    """RFC 9728 metadata so a remote MCP client (e.g. Alexa+) can discover how to authorize against /mcp."""
    base = os.environ.get("CARECIRCLE_PUBLIC_URL", str(request.base_url)).rstrip("/")
    issuers = [s for s in os.environ.get("CARECIRCLE_AUTH_SERVERS", "").split(",") if s]
    return JSONResponse({"resource": f"{base}/mcp", "authorization_servers": issuers,
                         "bearer_methods_supported": ["header"], "scopes_supported": ["carecircle"],
                         "resource_name": "CareCircle"})


@mcp.custom_route("/api/sweep", methods=["POST"])
async def api_sweep(request: Request):
    return JSONResponse({"alerts": service.sweep_missed()})


@mcp.custom_route("/api/clock", methods=["POST"])
async def api_clock(request: Request):
    body = await request.json()
    if "set" in body:
        clock.set_time(datetime.fromisoformat(body["set"]))
    elif "advance_minutes" in body:
        clock.advance(int(body["advance_minutes"]))
    return JSONResponse({"clock": clock.now().isoformat()})


@mcp.custom_route("/api/reset", methods=["POST"])
async def api_reset(request: Request):
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    if body.get("set"):
        clock.set_time(datetime.fromisoformat(body["set"]))
    service.reset()
    return JSONResponse({"ok": True, "clock": clock.now().isoformat()})
