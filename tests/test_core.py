"""Tests for google_calendars_core + the tool wrappers + the panel routes.

Google is faked at core._http, so nothing touches the network.
Run: pytest -q tests/
"""
import importlib
import json
import os
import stat
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDARS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GOOGLE_CALENDARS_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CALENDARS_CLIENT_SECRET", "csecret")
    import google_calendars_core as c
    importlib.reload(c)
    monkeypatch.setattr(c, "_plugin_settings", lambda: {})
    return c


class FakeGoogle:
    """Two accounts' worth of calendars/events; records every call."""

    def __init__(self, core):
        self.core = core
        self.calls = []
        self.tokens = {}  # access token -> email
        self.next_email = "me@work.com"
        self.grant_scope = " ".join(core.SCOPES)
        self.give_refresh = True
        self.refresh_fails = False
        self.calendars = {
            "me@work.com": [
                {"id": "me@work.com", "summary": "me@work.com", "primary": True, "accessRole": "owner",
                 "backgroundColor": "#111111", "timeZone": "America/Denver"},
                {"id": "holidays", "summary": "Holidays", "accessRole": "reader", "backgroundColor": "#222222"},
            ],
            "me@other.com": [
                {"id": "me@other.com", "summary": "me@other.com", "primary": True, "accessRole": "owner",
                 "backgroundColor": "#333333", "timeZone": "America/Denver"},
            ],
        }
        self.events = {
            "me@work.com": [{"id": "w1", "summary": "Standup", "start": {"dateTime": "2026-11-23T09:00:00-07:00"},
                             "end": {"dateTime": "2026-11-23T09:15:00-07:00"}, "recurringEventId": "rec"}],
            "holidays": [{"id": "h1", "summary": "Thanksgiving", "start": {"date": "2026-11-26"},
                          "end": {"date": "2026-11-27"}}],
            "me@other.com": [{"id": "o1", "summary": "Review", "start": {"dateTime": "2026-11-23T08:00:00-07:00"},
                              "end": {"dateTime": "2026-11-23T08:30:00-07:00"}},
                             {"id": "o2", "summary": "gone", "status": "cancelled",
                              "start": {"dateTime": "2026-11-23T10:00:00-07:00"},
                              "end": {"dateTime": "2026-11-23T10:30:00-07:00"}}],
        }
        self.broken = set()

    def __call__(self, method, url, *, params=None, form=None, body=None, token=None, timeout=20):
        self.calls.append({"method": method, "url": url, "params": params, "form": form, "body": body, "token": token})
        C = self.core
        if url == C.TOKEN_URL:
            if form["grant_type"] == "authorization_code":
                tok = f"at-{len(self.tokens)}"
                self.tokens[tok] = self.next_email
                out = {"access_token": tok, "expires_in": 3600, "scope": self.grant_scope}
                if self.give_refresh:
                    out["refresh_token"] = f"rt-{self.next_email}"
                return out
            if self.refresh_fails:
                raise C.CalendarError("Google returned 400: invalid_grant", status=400)
            email = form["refresh_token"][3:]
            tok = f"at-r{len(self.tokens)}"
            self.tokens[tok] = email
            return {"access_token": tok, "expires_in": 3600}
        if url == C.REVOKE_URL:
            return {}
        email = self.tokens.get(token)
        assert email, f"call without a valid token: {url}"
        if email in self.broken:
            raise C.CalendarError("Google returned 403: forbidden", status=403)
        path = url[len(C.API):]
        if path == "/users/me/calendarList/primary":
            return self.calendars[email][0]
        if path == "/users/me/calendarList":
            return {"items": self.calendars[email]}
        if path.startswith("/calendars/") and path.endswith("/events") and method == "GET":
            cal = urllib.parse.unquote(path.split("/")[2])
            return {"items": self.events.get(cal, [])}
        if path.startswith("/calendars/") and path.endswith("/events") and method == "POST":
            return {"id": "new1", **body}
        if "/events/" in path and method == "PATCH":
            return {"id": path.rsplit("/", 1)[1], "summary": body.get("summary", "x"),
                    "start": {"dateTime": "2026-11-23T09:00:00-07:00"}, "end": {"dateTime": "2026-11-23T10:00:00-07:00"}}
        if "/events/" in path and method == "DELETE":
            return {}
        if path == "/freeBusy":
            return {"calendars": {i["id"]: {"busy": [{"start": "s", "end": "e"}]} for i in body["items"]}}
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def google(core, monkeypatch):
    g = FakeGoogle(core)
    monkeypatch.setattr(core, "_http", g)
    return g


def connect(core, google, email):
    google.next_email = email
    started = core.start_sign_in()
    redirect = f"http://127.0.0.1:8765/?state={started['state']}&code=abc&scope=x"
    return core.finish_sign_in(redirect)


# ---------------------------------------------------------------- setup + store

def test_unconfigured_client_is_a_clear_error(core, monkeypatch):
    monkeypatch.delenv("GOOGLE_CALENDARS_CLIENT_ID")
    assert core.is_configured() is False
    with pytest.raises(core.CalendarError, match="Settings > Plugins"):
        core.start_sign_in()


def test_store_is_owner_only(core, google):
    connect(core, google, "me@work.com")
    p = core._store_path()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(p.parent).st_mode) == 0o700


def test_sign_in_url_uses_pkce_and_offline_access(core, google):
    s = core.start_sign_in()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(s["auth_url"]).query)
    assert q["code_challenge_method"] == ["S256"]
    assert q["access_type"] == ["offline"]
    assert q["scope"] == core.SCOPES
    assert q["redirect_uri"] == ["http://127.0.0.1:8765/"]
    assert q["state"] == [s["state"]]
    assert "consent" in q["prompt"][0]


def test_finish_saves_account_and_sends_verifier(core, google):
    acct = connect(core, google, "me@work.com")
    assert acct["email"] == "me@work.com"
    exchange = next(c for c in google.calls if c["form"] and c["form"].get("grant_type") == "authorization_code")
    assert len(exchange["form"]["code_verifier"]) > 40
    assert exchange["form"]["code"] == "abc"


def test_state_is_single_use(core, google):
    google.next_email = "me@work.com"
    s = core.start_sign_in()
    url = f"http://127.0.0.1:8765/?state={s['state']}&code=abc"
    core.finish_sign_in(url)
    with pytest.raises(core.CalendarError, match="expired or was already used"):
        core.finish_sign_in(url)


def test_expired_sign_in_is_refused(core, google, monkeypatch):
    s = core.start_sign_in()
    real = time.time
    monkeypatch.setattr(core.time, "time", lambda: real() + core.PENDING_TTL + 5)
    with pytest.raises(core.CalendarError, match="expired"):
        core.finish_sign_in(f"http://127.0.0.1:8765/?state={s['state']}&code=abc")


def test_bare_code_works_with_one_sign_in_in_flight(core, google):
    google.next_email = "me@work.com"
    core.start_sign_in()
    assert core.finish_sign_in("abc")["email"] == "me@work.com"


def test_google_error_in_redirect_is_reported(core, google):
    core.start_sign_in()
    with pytest.raises(core.CalendarError, match="access_denied"):
        core.finish_sign_in("http://127.0.0.1:8765/?error=access_denied")


def test_missing_calendar_scope_is_refused(core, google):
    google.grant_scope = "openid"
    with pytest.raises(core.CalendarError, match="Calendar access wasn't granted"):
        connect(core, google, "me@work.com")
    assert core.list_accounts() == []


def test_missing_refresh_token_is_refused(core, google):
    google.give_refresh = False
    with pytest.raises(core.CalendarError, match="refresh token"):
        connect(core, google, "me@work.com")


def test_reconnecting_same_account_updates_instead_of_duplicating(core, google):
    connect(core, google, "me@work.com")
    core.update_account("me@work.com", label="Work")
    connect(core, google, "me@work.com")
    accts = core.list_accounts()
    assert len(accts) == 1 and accts[0]["label"] == "Work"


def test_public_views_never_leak_tokens(core, google):
    connect(core, google, "me@work.com")
    blob = json.dumps([core.list_accounts(), core.list_calendars(),
                       core.list_events("2026-11-22", "2026-11-28")])
    assert "rt-" not in blob and "at-" not in blob and "csecret" not in blob


# ---------------------------------------------------------------- tokens

def test_expired_access_token_is_refreshed(core, google):
    connect(core, google, "me@work.com")
    with core._edit() as d:
        d["accounts"][0]["expires_at"] = 0
    core.list_calendars(refresh=True)
    assert any(c["form"] and c["form"].get("grant_type") == "refresh_token" for c in google.calls)


def test_revoked_refresh_token_says_sign_in_again(core, google):
    connect(core, google, "me@work.com")
    with core._edit() as d:
        d["accounts"][0]["expires_at"] = 0
    google.refresh_fails = True
    out = core.list_calendars(refresh=True)
    assert "needs to sign in again" in out[0]["error"]


# ---------------------------------------------------------------- events

def test_events_merge_across_accounts_sorted_and_skip_cancelled(core, google):
    connect(core, google, "me@work.com")
    connect(core, google, "me@other.com")
    res = core.list_events("2026-11-22", "2026-11-28")
    assert [e["summary"] for e in res["events"]] == ["Review", "Standup", "Thanksgiving"]
    assert res["errors"] == []
    standup = res["events"][1]
    assert standup["recurring"] and standup["writable"] and standup["color"] == "#111111"
    assert res["events"][2]["all_day"] and not res["events"][2]["writable"]


def test_hidden_calendars_are_skipped_unless_asked(core, google):
    connect(core, google, "me@work.com")
    core.update_account("me@work.com", hidden_calendars=["holidays"])
    assert "Thanksgiving" not in [e["summary"] for e in core.list_events("2026-11-22", "2026-11-28")["events"]]
    assert "Thanksgiving" in [e["summary"] for e in
                              core.list_events("2026-11-22", "2026-11-28", include_hidden=True)["events"]]


def test_one_broken_account_does_not_hide_the_others(core, google):
    connect(core, google, "me@work.com")
    connect(core, google, "me@other.com")
    google.broken.add("me@work.com")
    core._calendar_cache.clear()
    res = core.list_events("2026-11-22", "2026-11-28")
    assert [e["summary"] for e in res["events"]] == ["Review"]
    assert res["errors"] and "403" in res["errors"][0]["error"]


def test_event_fetch_failure_on_one_calendar_is_reported_not_raised(core, google):
    connect(core, google, "me@work.com")
    connect(core, google, "me@other.com")
    core.list_calendars()  # calendar lists cached; the event fetch is what fails
    google.broken.add("me@work.com")
    res = core.list_events("2026-11-22", "2026-11-28")
    assert [e["summary"] for e in res["events"]] == ["Review"]
    assert {e["calendar"] for e in res["errors"]} == {"me@work.com", "Holidays"}


def test_bare_dates_become_a_full_day_range(core, google):
    connect(core, google, "me@work.com")
    core.list_events("2026-11-22", "2026-11-28")
    ev_call = next(c for c in google.calls if c["url"].endswith("/events") and c["method"] == "GET")
    assert ev_call["params"]["timeMin"] == "2026-11-22T00:00:00Z"
    assert ev_call["params"]["timeMax"] == "2026-11-28T23:59:59Z"
    assert ev_call["params"]["singleEvents"] == "true"


def test_create_timed_event_uses_calendar_time_zone(core, google):
    connect(core, google, "me@work.com")
    ev = core.create_event("me@work.com", {"summary": "Lunch", "start": "2026-11-24T12:00:00",
                                           "end": "2026-11-24T13:00:00"})
    post = next(c for c in google.calls if c["method"] == "POST" and c["url"].endswith("/events"))
    assert post["body"]["start"] == {"dateTime": "2026-11-24T12:00:00", "timeZone": "America/Denver"}
    assert post["params"]["sendUpdates"] == "none"
    assert ev["summary"] == "Lunch"


def test_create_all_day_event(core, google):
    connect(core, google, "me@work.com")
    core.create_event("me@work.com", {"summary": "Off", "start": "2026-11-27", "end": "2026-11-28"})
    post = next(c for c in google.calls if c["method"] == "POST" and c["url"].endswith("/events"))
    assert post["body"]["start"] == {"date": "2026-11-27"} and post["body"]["end"] == {"date": "2026-11-28"}


def test_read_only_calendar_refuses_writes(core, google):
    connect(core, google, "me@work.com")
    with pytest.raises(core.CalendarError, match="read-only"):
        core.create_event("me@work.com", {"summary": "x", "start": "2026-11-27", "end": "2026-11-28"}, "holidays")
    with pytest.raises(core.CalendarError, match="read-only"):
        core.delete_event("me@work.com", "Holidays", "h1")


def test_account_lookup_by_label_and_unknown_account(core, google):
    connect(core, google, "me@work.com")
    core.update_account("me@work.com", label="Work")
    core.delete_event("work", "primary", "w1")
    with pytest.raises(core.CalendarError, match="No connected account matches 'nope'"):
        core.delete_event("nope", "primary", "w1")


def test_remove_account_revokes_and_forgets(core, google):
    connect(core, google, "me@work.com")
    core.remove_account("me@work.com")
    assert core.list_accounts() == []
    assert any(c["url"] == core.REVOKE_URL for c in google.calls)


# ---------------------------------------------------------------- tools

@pytest.fixture
def tools(core):
    import types
    pkg = types.ModuleType("gcal_pkg")
    pkg.__path__ = [str(ROOT)]
    sys.modules["gcal_pkg"] = pkg
    sys.modules["gcal_pkg.google_calendars_core"] = core
    spec = importlib.util.spec_from_file_location("gcal_pkg.tools_init", ROOT / "__init__.py",
                                                  submodule_search_locations=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "gcal_pkg"
    spec.loader.exec_module(mod)
    return mod


def test_tools_register_with_check_fn(tools):
    seen = []

    hooks = []

    class Ctx:
        def register_tool(self, **kw):
            seen.append(kw)

        def register_hook(self, name, fn):
            hooks.append(name)

    tools.register(Ctx())
    assert hooks == ["pre_llm_call"]
    assert [s["name"] for s in seen] == [t[0]["name"] for t in tools.TOOLS]
    assert all(callable(s["check_fn"]) for s in seen)


def test_tools_hidden_until_an_account_is_connected(tools, core, google):
    assert tools._available() is False
    connect(core, google, "me@work.com")
    assert tools._available() is True


def test_tool_errors_are_json_not_exceptions(tools, core, google):
    out = json.loads(dict((s["name"], f) for s, f in tools.TOOLS)["google_calendar_delete_event"]
                     and tools._wrap(tools._delete)({"account": "nobody", "calendar_id": "primary", "event_id": "x"}))
    assert out == {"ok": False, "error": "No connected account matches 'nobody' (accounts: none connected)."}


def test_events_tool_end_to_end(tools, core, google):
    connect(core, google, "me@work.com")
    out = json.loads(tools._wrap(tools._events)({"start": "2026-11-22", "end": "2026-11-28"}))
    assert out["ok"] and [e["summary"] for e in out["events"]] == ["Standup", "Thanksgiving"]


# ---------------------------------------------------------------- panel routes

def test_routes(core, google):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("gcal_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    sys.modules["google_calendars_core"] = core
    spec.loader.exec_module(api)
    app = fastapi.FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    assert c.get("/status").json()["configured"] is True
    google.next_email = "me@work.com"
    start = c.post("/auth/start").json()
    r = c.post("/auth/finish", json={"pasted": f"http://127.0.0.1:8765/?state={start['state']}&code=z"})
    assert r.status_code == 200 and r.json()["email"] == "me@work.com"
    assert c.get("/events", params={"start": "2026-11-22", "end": "2026-11-28"}).json()["events"]
    r = c.post("/events", json={"account_id": "me@work.com", "calendar_id": "holidays", "summary": "x",
                                "start": "2026-11-27", "end": "2026-11-28"})
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    assert c.request("DELETE", "/events/w1", params={"account_id": "me@work.com", "calendar_id": "primary"}).json() == {"ok": True}
    assert c.patch("/accounts/me@work.com", json={"hidden_calendars": ["holidays"]}).json()["hidden_calendars"] == ["holidays"]
    assert c.delete("/accounts/me@work.com").json() == {"ok": True}


# ---------------------------------------------------------------- reminders + briefings

from datetime import datetime, timezone as _tzmod, timedelta as _td

MT = _tzmod(_td(hours=-7))


def at(hh, mm, day=23):
    return datetime(2026, 11, day, hh, mm, tzinfo=MT)


@pytest.fixture
def settings(core, monkeypatch):
    vals = {"time_zone": ""}
    monkeypatch.setattr(core, "_plugin_settings", lambda: vals)
    monkeypatch.setattr(core, "_tz", lambda: MT)
    return vals


def test_lead_minutes_parsing(core, settings):
    settings["reminder_minutes"] = "10, 60 ,x, 0, 10"
    assert core.lead_minutes() == [60, 10]
    settings["reminder_minutes"] = "nonsense"
    assert core.lead_minutes() == [10]


def test_reminder_fires_once_inside_the_window(core, google, settings):
    connect(core, google, "me@work.com")
    assert core.due_reminders("desktop", at(8, 49)) == []
    first = core.due_reminders("desktop", at(8, 51))
    assert [r["summary"] for r in first] == ["Standup"] and first[0]["minutes"] == 9
    assert core.due_reminders("desktop", at(8, 52)) == []
    assert core.due_reminders("desktop", at(9, 0)) == []  # started


def test_desktop_and_chat_are_tracked_separately(core, google, settings):
    connect(core, google, "me@work.com")
    assert len(core.due_reminders("desktop", at(8, 55))) == 1
    assert len(core.due_reminders("chat", at(8, 55))) == 1


def test_two_leads_each_fire_but_catchup_sends_one(core, google, settings):
    settings["reminder_minutes"] = "60,10"
    connect(core, google, "me@work.com")
    assert len(core.due_reminders("desktop", at(8, 1))) == 1
    assert core.due_reminders("desktop", at(8, 30)) == []
    assert len(core.due_reminders("desktop", at(8, 51))) == 1
    # app was closed all morning: one reminder, not two
    assert len(core.due_reminders("chat", at(8, 55))) == 1


def test_reminders_disabled(core, google, settings):
    settings["reminders_enabled"] = False
    connect(core, google, "me@work.com")
    assert core.due_reminders("desktop", at(8, 55)) == []


def test_declined_events_skipped_by_setting(core, google, settings):
    connect(core, google, "me@work.com")
    google.events["me@work.com"][0]["attendees"] = [{"email": "me@work.com", "self": True, "responseStatus": "declined"}]
    assert core.due_reminders("desktop", at(8, 55)) == []
    settings["reminder_skip_declined"] = False
    assert len(core.due_reminders("chat", at(8, 55))) == 1


def test_all_day_events_skipped_by_setting(core, google, settings):
    settings["reminder_minutes"] = "60"
    connect(core, google, "me@work.com")
    google.events["me@work.com"][0]["start"] = {"date": "2026-11-24"}
    google.events["me@work.com"][0]["end"] = {"date": "2026-11-25"}
    assert core.due_reminders("desktop", at(23, 30)) == []
    settings["reminder_skip_all_day"] = False
    assert [r["summary"] for r in core.due_reminders("desktop", at(23, 30))] == ["Standup"]


def test_quiet_hours_swallow_reminders(core, google, settings):
    connect(core, google, "me@work.com")
    settings["reminder_quiet_hours"] = "22:00-09:00"
    assert core.in_quiet_hours(at(23, 0)) and core.in_quiet_hours(at(8, 55)) and not core.in_quiet_hours(at(9, 0))
    assert core.due_reminders("desktop", at(8, 55)) == []
    settings["reminder_quiet_hours"] = ""
    assert core.due_reminders("desktop", at(8, 56)) == []  # marked sent, not saved for later


def test_reminder_text(core):
    t = core.reminder_text([{"minutes": 5, "time": "9:00 AM", "summary": "Standup", "account_label": "Work",
                             "meet_link": "https://meet/x", "location": ""}])
    assert "In 5 min (9:00 AM): Standup · Work" in t and "Join: https://meet/x" in t
    assert core.reminder_text([]) == ""


def test_greeting_matching(core, settings):
    for m in ["good morning", "Good Morning!", "gm", "GM jerry", "morning, what's up", "good morning\nalso..."]:
        assert core.is_greeting(m), m
    for m in ["gmail is down", "mornings are hard", "what about good morning", ""]:
        assert not core.is_greeting(m), m
    settings["briefing_phrases"] = "hey jerry"
    assert core.is_greeting("Hey Jerry") and not core.is_greeting("good morning")


def test_greeting_context_once_per_day(core, google, settings):
    connect(core, google, "me@work.com")
    first = core.greeting_context("good morning", at(7, 0))
    assert first and "Standup" in first and "9:00 AM-9:15 AM" in first
    assert core.greeting_context("good morning", at(7, 30)) is None
    assert core.greeting_context("good morning", at(7, 0, day=24)) is not None
    settings["briefing_once_per_day"] = False
    assert core.greeting_context("gm", at(8, 0, day=24)) is not None


def test_greeting_context_off_and_non_greeting(core, google, settings):
    connect(core, google, "me@work.com")
    assert core.greeting_context("fix my PR", at(7, 0)) is None
    settings["briefing_greeting_enabled"] = False
    assert core.greeting_context("good morning", at(7, 0)) is None


def test_greeting_with_no_accounts_is_silent(core, settings):
    assert core.greeting_context("good morning", at(7, 0)) is None


def test_agenda_tomorrow_and_declined(core, google, settings):
    settings["briefing_include_tomorrow"] = True
    settings["briefing_once_per_day"] = False
    connect(core, google, "me@work.com")
    google.events["me@work.com"][0]["attendees"] = [{"email": "me@work.com", "self": True, "responseStatus": "declined"}]
    text = core.greeting_context("gm", at(7, 0))
    assert "Today" in text and "Tomorrow" in text and "Standup" not in text


def test_pre_llm_call_hook(tools, core, google, settings):
    connect(core, google, "me@work.com")
    assert tools._on_pre_llm_call(user_message="hello") is None
    out = tools._on_pre_llm_call(user_message="good morning", session_id="s")
    assert out and "context" in out


class FakeCron:
    def __init__(self):
        self.jobs = []

    def list_jobs(self, include_disabled=False):
        return list(self.jobs)

    def create_job(self, prompt, schedule, **kw):
        self.jobs.append({"id": str(len(self.jobs) + 1), "prompt": prompt,
                          "schedule": {"expr": schedule if " " in schedule else None, "raw": schedule}, **kw})

    def update_job(self, job_id, updates):
        j = next(j for j in self.jobs if j["id"] == job_id)
        if "schedule" in updates:
            j["schedule"] = {"expr": updates.pop("schedule")}
        j.update(updates)

    def remove_job(self, job_id):
        self.jobs = [j for j in self.jobs if j["id"] != job_id]
        return True


@pytest.fixture
def cron(core, settings, monkeypatch, tmp_path):
    import types
    fc = FakeCron()
    mod = types.ModuleType("cron")
    mod.jobs = fc
    monkeypatch.setitem(sys.modules, "cron", mod)
    monkeypatch.setitem(sys.modules, "cron.jobs", fc)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "_ensure_secret_passthrough", lambda: None)
    return fc


def test_sync_defaults_create_nothing(core, cron):
    assert core.sync_jobs() == {}
    assert cron.jobs == []


def test_sync_chat_reminders_job_is_script_only(core, cron, settings, tmp_path):
    settings["reminder_chat_target"] = "slack"
    assert core.sync_jobs() == {"reminders": "created"}
    j = cron.jobs[0]
    assert j["name"] == core.REMINDER_JOB and j["no_agent"] is True and j["deliver"] == "slack"
    script = tmp_path / "home" / "scripts" / core.REMINDER_SCRIPT
    assert "due_reminders('chat')" in script.read_text()
    assert core.sync_jobs() == {"reminders": "ok"}
    settings["reminder_chat_target"] = "telegram"
    assert core.sync_jobs() == {"reminders": "updated"} and cron.jobs[0]["deliver"] == "telegram"
    settings["reminder_chat_target"] = ""
    assert core.sync_jobs() == {"reminders": "removed"} and cron.jobs == []


def test_sync_reminders_off_removes_chat_job(core, cron, settings):
    settings["reminder_chat_target"] = "slack"
    core.sync_jobs()
    settings["reminders_enabled"] = False
    assert core.sync_jobs() == {"reminders": "removed"}


def test_sync_briefing_schedule(core, cron, settings):
    settings["briefing_schedule_enabled"] = True
    assert core.sync_jobs() == {"briefing": "created"}
    j = cron.jobs[0]
    assert j["schedule"]["raw"] == "30 7 * * 1-5" and j["enabled_toolsets"] == ["google_calendars"]
    assert core.sync_jobs() == {"briefing": "ok"}
    settings["briefing_time"] = "06:05"
    settings["briefing_days"] = "every day"
    assert core.sync_jobs() == {"briefing": "updated"} and cron.jobs[0]["schedule"]["expr"] == "5 6 * * *"
    settings["briefing_schedule_enabled"] = False
    assert core.sync_jobs() == {"briefing": "removed"} and cron.jobs == []


def test_sync_leaves_other_jobs_alone(core, cron, settings):
    cron.jobs.append({"id": "x", "name": "Jerry nightly backup", "schedule": {"expr": "0 18 * * *"}})
    settings["briefing_schedule_enabled"] = True
    core.sync_jobs()
    settings["briefing_schedule_enabled"] = False
    core.sync_jobs()
    assert [j["name"] for j in cron.jobs] == ["Jerry nightly backup"]


def test_reminder_routes(core, google, settings, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import importlib.util as iu
    connect(core, google, "me@work.com")
    monkeypatch.setitem(sys.modules, "google_calendars_core", core)
    spec = iu.spec_from_file_location("gcal_api_rem", ROOT / "dashboard" / "plugin_api.py")
    m = iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(core, "_now", lambda: at(8, 55))
    app = FastAPI()
    app.include_router(m.router)
    c = TestClient(app)
    assert c.get("/status").json()["reminders"] == {"enabled": True, "desktop": True, "minutes": [10]}
    assert [r["summary"] for r in c.get("/reminders/due").json()["reminders"]] == ["Standup"]
    assert c.get("/reminders/due").json()["reminders"] == []
    settings["reminders_desktop"] = False
    assert c.get("/reminders/due").json() == {"reminders": []}


def test_versions_agree():
    """plugin.yaml, the panel's VERSION and the newest CHANGELOG entry move together."""
    import re
    import yaml
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())["version"]
    js = re.search(r"const VERSION = '([^']+)'", (ROOT / "desktop" / "plugin.js").read_text()).group(1)
    log = re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M).group(1)
    assert manifest == js == log
