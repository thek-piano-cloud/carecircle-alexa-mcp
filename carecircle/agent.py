"""Alexa+ simulator brain.

On a real device Alexa+ is the agent: it hears the resident, picks one of our MCP tools and
speaks the result.  For the simulator we replace that LLM with a small, transparent intent
router.  Crucially, every action still goes through a real MCP client session over
Streamable HTTP (``/mcp``) — the router never touches the care logic directly.
"""
from __future__ import annotations

import os
import re
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MOODS = [
    (r"\b(wonderful|great|fantastic|excellent|really good)\b", 5),
    (r"\b(good|fine|well|happy|cheerful|better)\b", 4),
    (r"\b(okay|ok|alright|so-so|not bad)\b", 3),
    (r"\b(tired|sad|lonely|down|low|blue|worried|anxious|dizzy|weak|sick|unwell|not (so |very )?good)\b", 2),
    (r"\b(awful|terrible|miserable|horrible|hopeless)\b", 1),
]
NUMBERS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10}


def _clock_time(text: str) -> str | None:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", text, re.I)
    if m:
        h, mi, ap = int(m[1]), int(m[2] or 0), m[3].lower().replace(".", "")
        h = h % 12 + (12 if ap == "pm" else 0)
        return f"{h:02d}:{mi:02d}"
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        return f"{int(m[1]):02d}:{m[2]}"
    m = re.search(r"\b(?:at\s+)?(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(?:o'?clock)?\s*(in the )?(morning|afternoon|evening|night)?", text, re.I)
    if m and m[3]:
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12}
        h = words[m[1].lower()] % 12 + (12 if m[3].lower() != "morning" else 0)
        return f"{h:02d}:00"
    return None


MED_PHRASE = (r"(?:blood pressure|bp|pressure|blood sugar|sugar|diabetes|cholesterol|vitamin d|vitamin|statin|"
              r"amlodipine|metformin|atorvastatin)(?:\s+(?:pill|tablet|medicine|meds))?")


def _medicine(t: str) -> str:
    """The medicine the resident named ('blood pressure pill'), as Alexa+ would fill the tool argument."""
    m = re.search(MED_PHRASE, t)
    return m[0] if m else t


def route(text: str) -> tuple[str, dict[str, Any]] | None:
    """Map an utterance to (tool, arguments); None if nothing matches."""
    t = text.lower().strip()
    if not t:
        return None
    if re.search(r"\b(i fell|i['’]?ve fallen|fallen|can['’]?t breathe|chest (pain|hurts)|help me|i need help|emergency)\b", t):
        return "notify_family", {"message": f"Mrs. Han said: \"{text.strip()}\" Please call her immediately.", "urgency": "urgent"}
    if re.search(r"\b(skip|skipped|skipping|not taking|won['’]?t take|don['’]?t want to take)\b", t) and re.search(r"pill|medic|tablet|metformin|amlodipine|statin|vitamin|pressure|sugar", t):
        reason = ""
        m = re.search(r"(?:because|since|as)\s+(.*)$", t)
        if m:
            reason = m[1].strip(" .")
        return "skip_medication", {"medication": _medicine(t), "reason": reason}
    if re.search(r"\b(took|taken|had|swallowed|finished|just took|i've taken|take (another|an extra|one more))\b", t) and re.search(
            r"pill|medic|tablet|metformin|amlodipine|statin|vitamin|pressure|sugar|bp|cholesterol", t):
        args: dict[str, Any] = {"medication": _medicine(t)}
        if re.search(r"\b(doctor|nurse|pharmacist)\b", t):
            args["force"] = True
        return "confirm_medication", args
    if re.search(r"\bremind me\b", t):
        when = _clock_time(t)
        what = re.sub(r"^.*?remind me( to)?\s+", "", t)
        what = re.sub(r"\s*(at|by|around)\s+\d.*$", "", what)
        what = re.sub(r"\s*(at\s+)?(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)( o'?clock)?( in the (morning|afternoon|evening))?$", "", what).strip(" .")
        return "add_reminder", {"text": what or "do that", "when": when or "unspecified"}
    if re.search(r"\b(tell|text|message|call|let|inform|notify)\b.*\b(jisoo|minho|daughter|son|family)\b", t):
        msg = re.sub(r"^(please\s+)?(tell|text|message|let)\s+(my\s+)?(jisoo|minho|daughter|son|family)\s*(know\s*)?(that\s*)?", "", t).strip(" .")
        urgency = "important" if re.search(r"\b(urgent|important|soon|worried)\b", t) else "info"
        return "notify_family", {"message": f"Mrs. Han says: {msg or t}", "urgency": urgency}
    if re.search(r"how (was|has been|is|did).*(week|mum|mom|mother|grandma|she|her)|weekly (summary|digest)|family (digest|summary)|how am i doing|how have i been", t):
        return "get_family_digest", {"days": 7}
    if re.search(r"i (feel|am feeling|['’]?m feeling|['’]?m)\b|feeling|my .*(hurts|aches|pain)|\bpain\b|\bhurts\b", t):
        mood = 3
        for pat, val in MOODS:
            if re.search(pat, t):
                mood = val
                break
        pain = 0
        m = re.search(r"pain.*?(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten)\b|(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten)\s*(out of ten|/10)", t)
        if m:
            raw = m[1] or m[2]
            pain = int(raw) if raw.isdigit() else NUMBERS[raw]
        elif re.search(r"\b(terrible|awful|unbearable|severe)\b.*(pain|hurts)|(pain|hurts).*(terrible|awful|unbearable|severe)", t):
            pain = 8
        elif re.search(r"\b(hurts|aches|pain)\b", t):
            pain = 4
            mood = min(mood, 3)
        return "check_in", {"mood": mood, "pain": pain, "note": text.strip()[:200]}
    if re.search(r"what.*(today|plan|schedule|do i need|next|due)|did i take|my (plan|day|schedule)|any (pills|medicine)", t):
        return "get_daily_plan", {}
    return None


FALLBACK = ("I can help with your medicines, today's plan, how you feel, reminders, and messages to your family. "
            "Try: \"I took my blood pressure pill\".")


async def handle_utterance(text: str, mcp_url: str) -> dict[str, Any]:
    decision = route(text)
    trace: dict[str, Any] = {"server": mcp_url, "transport": "Streamable HTTP"}
    if decision is None:
        return {"utterance": text, "reply": FALLBACK, "tool": None, "trace": trace}
    tool, args = decision
    token = os.environ.get("CARECIRCLE_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _get_id):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            trace.update({"protocolVersion": init.protocolVersion, "serverName": init.serverInfo.name})
            result = await session.call_tool(tool, args)
    data = result.structuredContent or {}
    if result.isError:
        reply = "Sorry, something went wrong on my side. Please try again."
    else:
        reply = data.get("spoken") or "Done."
    return {"utterance": text, "reply": reply, "tool": tool, "arguments": args, "result": data,
            "isError": bool(result.isError), "trace": trace}
