"""Clock that can be pinned for repeatable demos.

In normal operation `now()` is the wall clock.  Setting CARECIRCLE_DEMO_NOW
(for example ``2026-10-23T08:05``) pins the clock to that moment; the demo UI
can then move it forward so a whole care day can be shown in a minute.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

_offset = timedelta(0)
_pinned: datetime | None = None


def _init_from_env() -> None:
    global _pinned, _offset
    raw = os.environ.get("CARECIRCLE_DEMO_NOW")
    if raw:
        pinned = datetime.fromisoformat(raw)
        _offset = pinned - datetime.now()
        _pinned = pinned


def now() -> datetime:
    """Current time, without seconds-level jitter in demo mode."""
    return (datetime.now() + _offset).replace(microsecond=0)


def advance(minutes: int) -> datetime:
    """Move the demo clock forward (demo mode only)."""
    global _offset
    _offset += timedelta(minutes=minutes)
    return now()


def set_time(moment: datetime) -> datetime:
    global _offset
    _offset = moment - datetime.now()
    return now()


def is_demo() -> bool:
    return _pinned is not None


_init_from_env()
