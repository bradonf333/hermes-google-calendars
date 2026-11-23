"""google-calendars: agent tools over every connected Google account.

The desktop panel (desktop/plugin.js + dashboard/plugin_api.py) and these
tools share google_calendars_core, so an account connected in the panel is
immediately usable by the agent.
"""
from __future__ import annotations

import json

try:
    from . import google_calendars_core as core
except ImportError:  # loaded outside a package (tests, tooling)
    import google_calendars_core as core

_ACCOUNT = {"type": "string", "description": "Account email or label (see google_calendar_calendars)."}
_WHEN = ("RFC3339 with offset, e.g. 2026-11-24T14:00:00-07:00, or YYYY-MM-DD for all-day. "
         "Use the user's local time zone.")

CALENDARS = {
    "name": "google_calendar_calendars",
    "description": "List the user's connected Google accounts and every calendar in each (id, name, writable, "
                   "visible in the panel). Call this first to learn account labels and calendar ids.",
    "parameters": {"type": "object", "properties": {
        "refresh": {"type": "boolean", "description": "Bypass the 10-minute calendar list cache."}}},
}

EVENTS = {
    "name": "google_calendar_events",
    "description": "List events across ALL connected Google accounts merged and sorted by start (recurring events "
                   "expanded). Defaults to the calendars shown in the panel; filter by account/calendar/text.",
    "parameters": {"type": "object", "properties": {
        "start": {"type": "string", "description": "Range start. " + _WHEN},
        "end": {"type": "string", "description": "Range end. " + _WHEN},
        "account": _ACCOUNT,
        "calendar_id": {"type": "string", "description": "Only this calendar (id from google_calendar_calendars)."},
        "query": {"type": "string", "description": "Free-text search (title, description, location, attendees)."},
        "include_hidden": {"type": "boolean", "description": "Also search calendars hidden in the panel."},
    }, "required": ["start", "end"]},
}

_EVENT_FIELDS = {
    "summary": {"type": "string", "description": "Title."},
    "start": {"type": "string", "description": "Start. " + _WHEN},
    "end": {"type": "string", "description": "End. " + _WHEN + " For all-day events the end date is exclusive."},
    "all_day": {"type": "boolean"},
    "time_zone": {"type": "string", "description": "IANA zone, e.g. America/Denver (defaults to the calendar's)."},
    "location": {"type": "string"},
    "description": {"type": "string"},
    "attendees": {"type": "array", "items": {"type": "string"}, "description": "Guest emails."},
    "send_updates": {"type": "string", "enum": ["none", "all", "externalOnly"],
                     "description": "Email guests about the change. Default none."},
}

CREATE = {
    "name": "google_calendar_create_event",
    "description": "Create an event on a specific account's calendar. Confirm the account, time and guests with the "
                   "user before creating anything that invites other people.",
    "parameters": {"type": "object", "properties": {
        "account": _ACCOUNT,
        "calendar_id": {"type": "string", "description": "Calendar id or name; default the account's primary."},
        **_EVENT_FIELDS,
    }, "required": ["account", "summary", "start", "end"]},
}

UPDATE = {
    "name": "google_calendar_update_event",
    "description": "Change an event (only the fields given). For a recurring event this changes only that "
                   "occurrence. Use account/calendar_id/event_id from google_calendar_events.",
    "parameters": {"type": "object", "properties": {
        "account": _ACCOUNT,
        "calendar_id": {"type": "string"},
        "event_id": {"type": "string"},
        "move_to_calendar_id": {"type": "string", "description": "Move the event to another calendar of the same account."},
        **_EVENT_FIELDS,
    }, "required": ["account", "calendar_id", "event_id"]},
}

DELETE = {
    "name": "google_calendar_delete_event",
    "description": "Delete an event (one occurrence if recurring). Confirm with the user first.",
    "parameters": {"type": "object", "properties": {
        "account": _ACCOUNT,
        "calendar_id": {"type": "string"},
        "event_id": {"type": "string"},
        "send_updates": {"type": "string", "enum": ["none", "all", "externalOnly"]},
    }, "required": ["account", "calendar_id", "event_id"]},
}

FREEBUSY = {
    "name": "google_calendar_free_busy",
    "description": "Busy time blocks per connected account in a range (visible calendars). Use to find open slots "
                   "across every account at once.",
    "parameters": {"type": "object", "properties": {
        "start": {"type": "string", "description": "RFC3339 with offset."},
        "end": {"type": "string", "description": "RFC3339 with offset."},
        "account": _ACCOUNT,
    }, "required": ["start", "end"]},
}

_FIELD_KEYS = ("summary", "start", "end", "all_day", "time_zone", "location", "description", "attendees")


def _fields(args: dict) -> dict:
    return {k: args[k] for k in _FIELD_KEYS if args.get(k) is not None}


def _wrap(fn):
    def handler(args: dict | None = None, **_kw) -> str:
        try:
            return json.dumps({"ok": True, **fn(args or {})}, default=str)
        except core.CalendarError as e:
            return json.dumps({"ok": False, "error": str(e)})
    return handler


def _calendars(a):
    return {"accounts": core.list_calendars(bool(a.get("refresh")))}


def _events(a):
    return core.list_events(a["start"], a["end"], account=a.get("account"), calendar_id=a.get("calendar_id"),
                            query=a.get("query"), include_hidden=bool(a.get("include_hidden")))


def _create(a):
    return {"event": core.create_event(a["account"], _fields(a), a.get("calendar_id") or "primary",
                                       a.get("send_updates") or "none")}


def _update(a):
    ev = None
    if _fields(a):
        ev = core.update_event(a["account"], a["calendar_id"], a["event_id"], _fields(a), a.get("send_updates") or "none")
    if a.get("move_to_calendar_id"):
        ev = core.move_event(a["account"], a["calendar_id"], a["event_id"], a["move_to_calendar_id"])
    if ev is None:
        raise core.CalendarError("Nothing to change.")
    return {"event": ev}


def _delete(a):
    core.delete_event(a["account"], a["calendar_id"], a["event_id"], a.get("send_updates") or "none")
    return {"deleted": a["event_id"]}


def _free_busy(a):
    return {"accounts": core.free_busy(a["start"], a["end"], a.get("account"))}


TOOLS = [
    (CALENDARS, _calendars),
    (EVENTS, _events),
    (CREATE, _create),
    (UPDATE, _update),
    (DELETE, _delete),
    (FREEBUSY, _free_busy),
]


def _available() -> bool:
    """Only offer the tools once the OAuth client is set up and an account is connected."""
    try:
        return core.is_configured() and bool(core.list_accounts())
    except Exception:
        return False


def _on_pre_llm_call(session_id=None, user_message="", **_kw):
    """Greeting briefing: when the message starts with a configured phrase, hand the model today's agenda."""
    try:
        ctx_text = core.greeting_context(user_message or "")
    except Exception:
        return None
    return {"context": ctx_text} if ctx_text else None


def register(ctx):
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    for schema, fn in TOOLS:
        ctx.register_tool(name=schema["name"], toolset="google_calendars", schema=schema, handler=_wrap(fn),
                          check_fn=_available, description=schema["description"], emoji="📅")
