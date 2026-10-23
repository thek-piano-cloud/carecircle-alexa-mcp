"""Care logic behind the MCP tools: plan, medication safety, check-ins, family alerts.

Everything here is plain Python over SQLite so it can be unit-tested without a
transport.  The MCP layer (``mcp_server.py``) is a thin, well-described wrapper.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import clock, notifier

DB_PATH = os.environ.get("CARECIRCLE_DB", str(Path(__file__).resolve().parent.parent / "carecircle.db"))
EMERGENCY = os.environ.get("CARECIRCLE_EMERGENCY_NUMBER", "your local emergency number (119 in Korea, 911 in the US)")

# name, dose, purpose, aliases, slot times, minimum hours between doses
MEDS = [
    ("Amlodipine", "5 mg", "blood pressure", ["blood pressure", "bp", "amlodipine", "pressure pill"], ["08:00"], 20),
    ("Metformin", "500 mg", "blood sugar", ["metformin", "sugar", "diabetes", "blood sugar"], ["08:00", "18:30"], 6),
    ("Vitamin D", "1000 IU", "bones", ["vitamin d", "vitamin", "bone"], ["08:00"], 20),
    ("Atorvastatin", "20 mg", "cholesterol", ["atorvastatin", "statin", "cholesterol"], ["21:00"], 20),
]
EARLY_MIN = 90      # a slot opens this many minutes before its time
LATE_MIN = 120      # a slot counts as missed this many minutes after its time

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meds(id INTEGER PRIMARY KEY, name TEXT, dose TEXT, purpose TEXT,
                aliases TEXT, slots TEXT, min_gap_h INTEGER);
            CREATE TABLE IF NOT EXISTS dose_log(id INTEGER PRIMARY KEY, med_id INTEGER, slot TEXT,
                taken_at TEXT, status TEXT, note TEXT);
            CREATE TABLE IF NOT EXISTS checkins(id INTEGER PRIMARY KEY, ts TEXT, mood INTEGER, pain INTEGER, note TEXT);
            CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, ts TEXT, urgency TEXT, message TEXT,
                recipients TEXT, source TEXT);
            CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, text TEXT, due TEXT, done INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS appointments(id INTEGER PRIMARY KEY, title TEXT, start TEXT, place TEXT);
            """
        )
        if _conn.execute("SELECT COUNT(*) FROM meds").fetchone()[0] == 0:
            _seed(_conn)
    return _conn


def reset() -> None:
    """Drop everything and re-seed (used by tests and the demo reset button)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        if DB_PATH != ":memory:" and Path(DB_PATH).exists():
            Path(DB_PATH).unlink()
        _db()


def _seed(c: sqlite3.Connection) -> None:
    for name, dose, purpose, aliases, slots, gap in MEDS:
        c.execute("INSERT INTO meds(name,dose,purpose,aliases,slots,min_gap_h) VALUES(?,?,?,?,?,?)",
                  (name, dose, purpose, json.dumps(aliases), json.dumps(slots), gap))
    today = clock.now().date()
    # A realistic past week: mostly on time, a few missed evening doses, a low-mood stretch.
    mood = {6: (4, 1), 5: (4, 0), 4: (3, 2), 3: (2, 3), 2: (2, 5), 1: (2, 2)}
    missed = {(3, "Atorvastatin"), (2, "Metformin"), (2, "Atorvastatin")}
    meds = c.execute("SELECT * FROM meds").fetchall()
    for back in range(6, 0, -1):
        d = today - timedelta(days=back)
        for m in meds:
            for s in json.loads(m["slots"]):
                slot = f"{d.isoformat()} {s}"
                if (back, m["name"]) in missed and s >= "18:00":
                    c.execute("INSERT INTO dose_log(med_id,slot,taken_at,status,note) VALUES(?,?,?,?,?)",
                              (m["id"], slot, None, "missed", None))
                else:
                    t = datetime.fromisoformat(slot) + timedelta(minutes=5 + (back * 3) % 20)
                    c.execute("INSERT INTO dose_log(med_id,slot,taken_at,status,note) VALUES(?,?,?,?,?)",
                              (m["id"], slot, t.isoformat(), "taken", None))
        md, pn = mood[back]
        c.execute("INSERT INTO checkins(ts,mood,pain,note) VALUES(?,?,?,?)",
                  (datetime.combine(d, datetime.min.time()).replace(hour=19).isoformat(), md, pn, None))
    days_to_friday = (4 - today.weekday()) % 7 or 7
    c.execute("INSERT INTO appointments(title,start,place) VALUES(?,?,?)",
              ("Cardiology check-up with Dr. Lee", (today + timedelta(days=days_to_friday)).isoformat() + "T10:30",
               "Seoul Medical Center, 3rd floor"))
    c.execute("INSERT INTO outbox(ts,urgency,message,recipients,source) VALUES(?,?,?,?,?)",
              (datetime.combine(today - timedelta(days=2), datetime.min.time()).replace(hour=21, minute=40).isoformat(),
               "info", "Mrs. Han missed her evening Metformin and Atorvastatin two days ago.", "Jisoo", "auto"))
    c.commit()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _meds() -> list[dict[str, Any]]:
    out = []
    for r in _db().execute("SELECT * FROM meds ORDER BY id"):
        out.append({"id": r["id"], "name": r["name"], "dose": r["dose"], "purpose": r["purpose"],
                    "aliases": json.loads(r["aliases"]), "slots": json.loads(r["slots"]), "min_gap_h": r["min_gap_h"]})
    return out


def _hm(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _when(dt: datetime, now: datetime) -> str:
    """'8:05 AM' today, 'yesterday at 9:08 PM' otherwise."""
    if dt.date() == now.date():
        return _hm(dt)
    if dt.date() == now.date() - timedelta(days=1):
        return f"yesterday at {_hm(dt)}"
    return f"{dt.strftime('%A')} at {_hm(dt)}"


def _join(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _slot_dt(day: date, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day.isoformat()} {hhmm}")


def _logged(med_id: int, slot: str) -> sqlite3.Row | None:
    return _db().execute("SELECT * FROM dose_log WHERE med_id=? AND slot=? ORDER BY id DESC", (med_id, slot)).fetchone()


def _slot_state(med: dict, hhmm: str, at: datetime) -> dict[str, Any]:
    slot_dt = _slot_dt(at.date(), hhmm)
    row = _logged(med["id"], slot_dt.isoformat(sep=" "))
    if row and row["status"] == "taken":
        state = "taken"
    elif row and row["status"] == "skipped":
        state = "skipped"
    elif at < slot_dt - timedelta(minutes=EARLY_MIN):
        state = "upcoming"
    elif at <= slot_dt + timedelta(minutes=LATE_MIN):
        state = "due"
    else:
        state = "missed"
    return {"medication": med["name"], "dose": med["dose"], "purpose": med["purpose"], "time": hhmm,
            "time_text": _hm(slot_dt), "state": state,
            "taken_at": row["taken_at"] if row and row["status"] == "taken" else None}


def _match(text: str) -> list[dict]:
    t = text.lower()
    scored = []
    for m in _meds():
        score = 0
        for a in m["aliases"] + [m["name"].lower(), m["purpose"]]:
            if a and a in t:
                score = max(score, len(a))
        if score:
            scored.append((score, m))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [m for s, m in scored if s == best]


def _emit(urgency: str, message: str, source: str) -> dict[str, Any]:
    recipients = ["Jisoo"] if urgency == "info" else ["Jisoo", "Minho"]
    channel = {"info": "app notification", "important": "push + SMS", "urgent": "push + SMS + phone call"}[urgency]
    ts = clock.now().isoformat()
    cur = _db().execute("INSERT INTO outbox(ts,urgency,message,recipients,source) VALUES(?,?,?,?,?)",
                        (ts, urgency, message, ",".join(recipients), source))
    _db().commit()
    delivery = notifier.deliver(urgency, message)
    return {"id": cur.lastrowid, "ts": ts, "urgency": urgency, "channel": channel, "recipients": recipients,
            "message": message, "source": source, "delivery": delivery}


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------

def daily_plan() -> dict[str, Any]:
    with _lock:
        at = clock.now()
        slots = []
        for m in _meds():
            for hhmm in m["slots"]:
                slots.append(_slot_state(m, hhmm, at))
        slots.sort(key=lambda s: s["time"])
        appts = [dict(r) for r in _db().execute("SELECT title,start,place FROM appointments ORDER BY start")
                 if r["start"][:10] >= at.date().isoformat()][:2]
        rem = [dict(r) for r in _db().execute("SELECT id,text,due FROM reminders WHERE done=0 ORDER BY due")]
        due = [s for s in slots if s["state"] == "due"]
        missed = [s for s in slots if s["state"] == "missed"]
        nxt = next((s for s in slots if s["state"] == "upcoming"), None)
        if due:
            names = _join(sorted({s["medication"] for s in due}))
            spoken = f"It's time for your {names}."
        elif missed:
            spoken = f"You haven't logged {missed[0]['medication']} from {missed[0]['time_text']}. Have you taken it?"
        elif nxt:
            spoken = f"You're all caught up. Next is {nxt['medication']} at {nxt['time_text']}."
        else:
            spoken = "You're all caught up for today."
        if appts:
            when = datetime.fromisoformat(appts[0]["start"])
            spoken += f" Your next appointment is {appts[0]['title']} on {when.strftime('%A')} at {_hm(when)}."
        return {"date": at.date().isoformat(), "now": at.isoformat(), "medications": slots, "appointments": appts,
                "reminders": rem, "spoken": spoken}


def confirm_medication(medication: str, force: bool = False) -> dict[str, Any]:
    """Log a dose after a double-dose / timing safety check."""
    with _lock:
        at = clock.now()
        found = _match(medication)
        if not found:
            names = ", ".join(m["name"] for m in _meds())
            return {"status": "unknown_medication", "logged": False, "options": [m["name"] for m in _meds()],
                    "spoken": f"I don't have that medicine in your plan. Your medicines are {names}."}
        if len(found) > 1:
            names = " or ".join(m["name"] for m in found)
            return {"status": "needs_clarification", "logged": False, "options": [m["name"] for m in found],
                    "spoken": f"Which one did you take, {names}?"}
        med = found[0]
        states = [_slot_state(med, s, at) for s in med["slots"]]
        open_slot = next((s for s in states if s["state"] in ("due", "missed")), None)
        # double-dose guard: a recent dose inside the minimum gap blocks logging another one
        last = _db().execute("SELECT taken_at FROM dose_log WHERE med_id=? AND status='taken' ORDER BY taken_at DESC LIMIT 1",
                             (med["id"],)).fetchone()
        guarded = None   # the rule a doctor-directed (force) dose overrides; the family is always told
        if last and last["taken_at"]:
            when = datetime.fromisoformat(last["taken_at"])
            if at - when < timedelta(hours=med["min_gap_h"]):
                guarded = f"inside the {med['min_gap_h']}-hour gap after the last one at {_hm(when)}"
            if guarded and not force:
                return {"status": "already_taken", "logged": False, "medication": med["name"],
                        "last_taken": last["taken_at"], "min_gap_hours": med["min_gap_h"],
                        "spoken": f"Hold on. You already took your {med['name']} {'at ' if when.date() == at.date() else ''}{_when(when, at)}. "
                                  f"Please don't take another one. I haven't changed anything."}
        if open_slot is None:
            nxt = next((s for s in states if s["state"] == "upcoming"), None)
            if nxt and not force:
                return {"status": "too_early", "logged": False, "medication": med["name"], "next_time": nxt["time"],
                        "spoken": f"It's a bit early for your {med['name']}. It's scheduled for {nxt['time_text']}. "
                                  f"I haven't logged it."}
            if nxt and not guarded:
                guarded = f"before its scheduled time ({nxt['time_text']})"
            if not nxt:
                return {"status": "nothing_due", "logged": False, "medication": med["name"],
                        "spoken": f"You've already taken all of today's {med['name']}."}
            open_slot = nxt
        slot_dt = _slot_dt(at.date(), open_slot["time"])
        _db().execute("INSERT INTO dose_log(med_id,slot,taken_at,status,note) VALUES(?,?,?,?,?)",
                      (med["id"], slot_dt.isoformat(sep=" "), at.isoformat(), "taken", "forced" if force else None))
        _db().commit()
        late = open_slot["state"] == "missed"
        alert = None
        if force and guarded:
            alert = _emit("important", f"Mrs. Han logged {med['name']} {med['dose']} at {_hm(at)}, {guarded}, "
                          "saying her doctor told her to. Please double-check with her.", "confirm_medication")
        return {"status": "logged", "logged": True, "medication": med["name"], "dose": med["dose"],
                "slot": open_slot["time"], "taken_at": at.isoformat(), "late": late, "override": bool(force and guarded),
                "family_alert": alert,
                "spoken": (f"Okay. Because your doctor said so, I've logged your {med['name']} at {_hm(at)}. "
                           f"I've also told Jisoo and Minho, just so they know." if alert else
                           f"Got it. I've marked your {med['name']} {med['dose']} as taken at {_hm(at)}."
                           + (" That was a little late, but that's okay." if late else ""))}


def skip_medication(medication: str, reason: str = "") -> dict[str, Any]:
    with _lock:
        at = clock.now()
        found = _match(medication)
        if len(found) != 1:
            return {"status": "needs_clarification", "logged": False, "options": [m["name"] for m in (found or _meds())],
                    "spoken": "Which medicine are you skipping?"}
        med = found[0]
        states = [_slot_state(med, s, at) for s in med["slots"]]
        slot = next((s for s in states if s["state"] in ("due", "missed", "upcoming")), None)
        if not slot:
            return {"status": "nothing_due", "logged": False, "spoken": f"There's no {med['name']} left to skip today."}
        slot_dt = _slot_dt(at.date(), slot["time"])
        _db().execute("INSERT INTO dose_log(med_id,slot,taken_at,status,note) VALUES(?,?,?,?,?)",
                      (med["id"], slot_dt.isoformat(sep=" "), None, "skipped", reason or None))
        _db().commit()
        recent = _db().execute("SELECT COUNT(*) FROM dose_log WHERE med_id=? AND status IN ('skipped','missed') AND slot>=?",
                               (med["id"], (at - timedelta(days=3)).isoformat(sep=" "))).fetchone()[0]
        alert = None
        if recent >= 2:
            alert = _emit("important", f"Mrs. Han has skipped or missed {med['name']} {recent} times in the last 3 days"
                          + (f" (latest reason: {reason})." if reason else "."), "skip_medication")
        return {"status": "skipped", "logged": True, "medication": med["name"], "family_alert": alert,
                "spoken": f"Okay, I've noted that you skipped {med['name']}."
                          + (" I've also let Jisoo know, since it has happened a few times." if alert else "")}


MOOD_WORDS = {1: "very low", 2: "low", 3: "so-so", 4: "good", 5: "great"}


def check_in(mood: int, pain: int = 0, note: str = "") -> dict[str, Any]:
    with _lock:
        mood = max(1, min(5, int(mood)))
        pain = max(0, min(10, int(pain)))
        at = clock.now()
        _db().execute("INSERT INTO checkins(ts,mood,pain,note) VALUES(?,?,?,?)", (at.isoformat(), mood, pain, note or None))
        _db().commit()
        recent = [r["mood"] for r in _db().execute("SELECT mood FROM checkins ORDER BY ts DESC LIMIT 3")]
        alerts = []
        if pain >= 7:
            alerts.append(_emit("urgent", f"Mrs. Han reports strong pain ({pain}/10)"
                                + (f": \"{note}\"" if note else "") + ". Please call her now.", "check_in"))
        elif mood <= 2 and len(recent) >= 3 and all(m <= 2 for m in recent):
            alerts.append(_emit("important", "Mrs. Han has felt low for three check-ins in a row. A call or visit would help.", "check_in"))
        elif mood <= 2:
            alerts.append(_emit("info", f"Mrs. Han feels {MOOD_WORDS[mood]} today" + (f" (\"{note}\")." if note else "."), "check_in"))
        if pain >= 7:
            spoken = (f"I'm sorry you're in that much pain. I've alerted Jisoo and Minho and they will call you. "
                      f"If it feels like an emergency, call {EMERGENCY} right now.")
        elif alerts and alerts[0]["urgency"] == "important":
            spoken = "I'm sorry you've been feeling low lately. I let Jisoo know so she can spend some time with you."
        elif alerts:
            spoken = f"Thank you for telling me. I'm sorry you feel {MOOD_WORDS[mood]}. I let Jisoo know, in case she'd like to call."
        elif mood >= 4:
            spoken = f"I'm glad you're feeling {MOOD_WORDS[mood]}. I've saved your check-in."
        else:
            spoken = "Thanks for checking in. I've saved it."
        return {"status": "saved", "mood": mood, "pain": pain, "alerts": alerts, "spoken": spoken}


def notify_family(message: str, urgency: str = "info") -> dict[str, Any]:
    with _lock:
        if urgency not in ("info", "important", "urgent"):
            urgency = "info"
        text = message.strip()
        if re.search(r"\b(fell|fallen|fall|can'?t breathe|chest pain|stroke|unconscious|bleeding)\b", text, re.I):
            urgency = "urgent"
        sent = _emit(urgency, text, "notify_family")
        spoken = {"info": "I've sent that to Jisoo.",
                  "important": "I've sent that to Jisoo and Minho as an important message.",
                  "urgent": f"I'm alerting Jisoo and Minho right now. "
                            f"If you need help immediately, call {EMERGENCY}."}[urgency]
        return {"status": "sent", "notification": sent, "spoken": spoken}


def add_reminder(text: str, when: str) -> dict[str, Any]:
    """`when` is an ISO datetime or a clock time such as '15:00' (today, or tomorrow if already past)."""
    with _lock:
        at = clock.now()
        due = None
        try:
            due = datetime.fromisoformat(when)
        except ValueError:
            m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", when)
            if m:
                due = at.replace(hour=int(m[1]), minute=int(m[2]), second=0)
                if due <= at:
                    due += timedelta(days=1)
        if due is None:
            return {"status": "bad_time", "spoken": "What time should I remind you? You can say something like three in the afternoon."}
        _db().execute("INSERT INTO reminders(text,due) VALUES(?,?)", (text.strip(), due.isoformat()))
        _db().commit()
        day = "today" if due.date() == at.date() else "tomorrow" if due.date() == at.date() + timedelta(days=1) else due.strftime("%A")
        return {"status": "saved", "text": text.strip(), "due": due.isoformat(),
                "spoken": f"Okay, I'll remind you to {text.strip()} {day} at {_hm(due)}."}


def family_digest(days: int = 7) -> dict[str, Any]:
    with _lock:
        at = clock.now()
        days = max(1, min(30, int(days)))
        since = (at.date() - timedelta(days=days - 1)).isoformat()
        rows = _db().execute("SELECT d.*, m.name FROM dose_log d JOIN meds m ON m.id=d.med_id "
                             "WHERE substr(d.slot,1,10)>=?", (since,)).fetchall()
        taken = sum(1 for r in rows if r["status"] == "taken")
        missed = [{"medication": r["name"], "slot": r["slot"], "status": r["status"]}
                  for r in rows if r["status"] in ("missed", "skipped")]
        by_med: dict[str, list[int]] = {}
        for r in rows:
            by_med.setdefault(r["name"], [0, 0])
            by_med[r["name"]][1] += 1
            by_med[r["name"]][0] += r["status"] == "taken"
        # today's slots already past their window and still unlogged count as missed
        for m in _meds():
            for s in m["slots"]:
                if _slot_state(m, s, at)["state"] == "missed" and not _logged(m["id"], f"{at.date().isoformat()} {s}"):
                    missed.append({"medication": m["name"], "slot": f"{at.date().isoformat()} {s}", "status": "missed"})
                    by_med.setdefault(m["name"], [0, 0])
                    by_med[m["name"]][1] += 1
        total = taken + len(missed)
        adherence = round(100 * taken / total) if total else 100
        checks = [dict(r) for r in _db().execute("SELECT ts,mood,pain FROM checkins WHERE substr(ts,1,10)>=? ORDER BY ts", (since,))]
        moods = [c["mood"] for c in checks]
        avg = round(sum(moods) / len(moods), 1) if moods else None
        trend = "no data"
        if len(moods) >= 4:
            half = len(moods) // 2
            a, b = sum(moods[:half]) / half, sum(moods[half:]) / (len(moods) - half)
            trend = "improving" if b - a >= 0.5 else "declining" if a - b >= 0.5 else "steady"
        alerts = [dict(r) for r in _db().execute("SELECT ts,urgency,message FROM outbox WHERE substr(ts,1,10)>=? ORDER BY ts DESC", (since,))]
        worst = min(by_med.items(), key=lambda kv: kv[1][0] / kv[1][1]) if by_med else None
        headline = f"{adherence}% of doses taken over {days} days"
        if worst and worst[1][0] < worst[1][1]:
            headline += f"; {worst[0]} was missed most often"
        if avg is not None:
            headline += f". Mood averaged {avg} out of 5 and is {trend}."
        return {"days": days, "adherence_percent": adherence, "doses_taken": taken, "doses_missed": len(missed),
                "missed": missed, "mood_average": avg, "mood_trend": trend, "checkins": checks,
                "alerts": alerts, "headline": headline, "spoken": headline.replace("%", " percent")}


def sweep_missed() -> list[dict[str, Any]]:
    """Proactive watcher: a dose still unlogged LATE_MIN after its time is marked missed and the family is told once.

    Nobody has to speak first. ``python -m carecircle`` runs this every minute; it is idempotent per slot.
    """
    with _lock:
        at = clock.now()
        sent = []
        for m in _meds():
            for hhmm in m["slots"]:
                slot = f"{at.date().isoformat()} {hhmm}"
                if _slot_state(m, hhmm, at)["state"] != "missed" or _logged(m["id"], slot):
                    continue
                _db().execute("INSERT INTO dose_log(med_id,slot,taken_at,status,note) VALUES(?,?,?,?,?)",
                              (m["id"], slot, None, "missed", "watcher"))
                _db().commit()
                late_h = round((at - _slot_dt(at.date(), hhmm)).total_seconds() / 3600, 1)
                sent.append(_emit("important", f"Mrs. Han hasn't taken her {m['name']} {m['dose']} from "
                                  f"{_hm(_slot_dt(at.date(), hhmm))} ({late_h:g} hours ago) and hasn't said anything. "
                                  "A quick call could help.", "watcher"))
        return sent


def outbox(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _db().execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))]
