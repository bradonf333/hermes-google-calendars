"""google-calendars plugin backend: mounted at /api/plugins/google-calendars.

Thin HTTP wrapper over google_calendars_core (one level up), which the agent
tools share. Python is only imported when the plugin is in `plugins.enabled`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import google_calendars_core as core  # noqa: E402

router = APIRouter()


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except core.CalendarError as e:
        raise HTTPException(e.status, str(e))


@router.get("/status")
def status():
    return {"configured": core.is_configured(), "accounts": _run(core.list_accounts),
            "redirect_uri": core._plugin_settings().get("redirect_uri") or core.DEFAULT_REDIRECT}


# ---- accounts ------------------------------------------------------------

@router.post("/auth/start")
def auth_start():
    return _run(core.start_sign_in)


class Finish(BaseModel):
    pasted: str
    state: str | None = None


@router.post("/auth/finish")
def auth_finish(body: Finish):
    return _run(core.finish_sign_in, body.pasted, body.state)


class AccountPatch(BaseModel):
    label: str | None = None
    color: str | None = None
    hidden_calendars: list[str] | None = None


@router.patch("/accounts/{account_id}")
def account_patch(account_id: str, body: AccountPatch):
    return _run(core.update_account, account_id, body.label, body.color, body.hidden_calendars)


@router.delete("/accounts/{account_id}")
def account_delete(account_id: str):
    _run(core.remove_account, account_id)
    return {"ok": True}


# ---- calendars + events --------------------------------------------------

@router.get("/calendars")
def calendars(refresh: int = 0):
    return {"accounts": _run(core.list_calendars, bool(refresh))}


@router.get("/events")
def events(start: str, end: str, q: str | None = None):
    return _run(core.list_events, start, end, query=q)


class EventIn(BaseModel):
    account_id: str
    calendar_id: str = "primary"
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    start: str | None = None
    end: str | None = None
    all_day: bool | None = None
    time_zone: str | None = None
    attendees: list[str] | None = None
    send_updates: str = "none"
    destination_calendar_id: str | None = None


def _fields(body: EventIn) -> dict:
    return body.model_dump(include={"summary", "description", "location", "start", "end",
                                    "all_day", "time_zone", "attendees"}, exclude_none=True)


@router.post("/events")
def event_create(body: EventIn):
    return _run(core.create_event, body.account_id, _fields(body), body.calendar_id, body.send_updates)


@router.patch("/events/{event_id}")
def event_update(event_id: str, body: EventIn):
    fields = _fields(body)
    ev = None
    if fields:
        ev = _run(core.update_event, body.account_id, body.calendar_id, event_id, fields, body.send_updates)
    if body.destination_calendar_id and body.destination_calendar_id != body.calendar_id:
        ev = _run(core.move_event, body.account_id, body.calendar_id, event_id, body.destination_calendar_id)
    if ev is None:
        raise HTTPException(400, "Nothing to change.")
    return ev


@router.delete("/events/{event_id}")
def event_delete(event_id: str, account_id: str, calendar_id: str, send_updates: str = "none"):
    _run(core.delete_event, account_id, calendar_id, event_id, send_updates)
    return {"ok": True}
