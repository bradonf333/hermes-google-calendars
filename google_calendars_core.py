"""Shared core for the google-calendars plugin: settings, the account store,
OAuth (PKCE + loopback redirect, finished by pasting the redirected URL),
and a small Google Calendar v3 client.

Used by both halves of the package:
  - dashboard/plugin_api.py (the desktop panel's backend, runs in the gateway)
  - __init__.py tools (the agent's calendar tools)

Stdlib only, so the plugin needs no extra Python dependencies.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

PLUGIN_ID = "google-calendars"
SECRET_ENV = "GOOGLE_CALENDARS_CLIENT_SECRET"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"
DEFAULT_REDIRECT = "http://127.0.0.1:8765/"
PENDING_TTL = 15 * 60  # seconds a started sign-in stays valid
CALENDAR_CACHE_TTL = 10 * 60

ACCOUNT_COLORS = ["#4285f4", "#a142f4", "#0b8043", "#f4511e", "#e67c73", "#039be5", "#f6bf26", "#8e24aa"]


class CalendarError(Exception):
    """A user-facing error (message is safe to show; never contains tokens)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- settings

def _plugin_settings() -> dict:
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
        return ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID, {}).get("settings") or {}
    except Exception:
        return {}


def _secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret

        val = get_secret(name)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(name, "")


def client_config() -> dict:
    """The user's own OAuth client. Raises CalendarError if it isn't set up yet."""
    s = _plugin_settings()
    client_id = (s.get("client_id") or os.environ.get("GOOGLE_CALENDARS_CLIENT_ID") or "").strip()
    client_secret = (_secret(SECRET_ENV) or "").strip()
    redirect = (s.get("redirect_uri") or DEFAULT_REDIRECT).strip()
    if not client_id or not client_secret:
        raise CalendarError(
            "Google OAuth client not set up yet: add the Client ID and Client secret under "
            "Settings > Plugins > Google Calendars (see the README).",
            status=409,
        )
    return {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect}


def is_configured() -> bool:
    try:
        client_config()
        return True
    except CalendarError:
        return False


# ---------------------------------------------------------------- storage

def data_dir() -> Path:
    override = os.environ.get("GOOGLE_CALENDARS_DATA_DIR")
    if override:
        p = Path(override)
    else:
        try:
            from plugins.plugin_storage import plugin_data_dir

            p = plugin_data_dir(PLUGIN_ID)
        except Exception:
            home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
            p = home / "plugin-data" / PLUGIN_ID
    p.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(p, 0o700)
    return p


_lock = threading.RLock()


@contextlib.contextmanager
def _file_lock():
    """Cross-process lock (the gateway and agent processes share the store)."""
    with _lock:
        lock_path = data_dir() / ".lock"
        fh = open(lock_path, "a+")
        try:
            try:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX)
            except ImportError:  # Windows: in-process lock only
                pass
            yield
        finally:
            fh.close()


def _store_path() -> Path:
    return data_dir() / "accounts.json"


def _read() -> dict:
    p = _store_path()
    if not p.exists():
        return {"accounts": [], "pending": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CalendarError(f"Account store is unreadable: {p}", status=500)
    data.setdefault("accounts", [])
    data.setdefault("pending", {})
    return data


def _write(data: dict) -> None:
    p = _store_path()
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".accounts.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _edit():
    with _file_lock():
        data = _read()
        yield data
        _write(data)


def public_account(a: dict) -> dict:
    """Account fields that are safe to send to the UI / the model."""
    return {
        "id": a["id"],
        "email": a["email"],
        "label": a.get("label") or a["email"],
        "color": a.get("color"),
        "hidden_calendars": a.get("hidden_calendars", []),
        "added": a.get("added"),
    }


def list_accounts() -> list[dict]:
    with _file_lock():
        return [public_account(a) for a in _read()["accounts"]]


def _find(accounts: list[dict], key: str) -> dict:
    k = (key or "").strip().lower()
    for a in accounts:
        if k in (a["id"].lower(), a["email"].lower(), (a.get("label") or "").lower()):
            return a
    names = ", ".join(a.get("label") or a["email"] for a in accounts) or "none connected"
    raise CalendarError(f"No connected account matches '{key}' (accounts: {names}).", status=404)


def update_account(key: str, label: str | None = None, color: str | None = None,
                   hidden_calendars: list[str] | None = None) -> dict:
    with _edit() as data:
        a = _find(data["accounts"], key)
        if label is not None:
            a["label"] = label.strip() or a["email"]
        if color is not None:
            a["color"] = color
        if hidden_calendars is not None:
            a["hidden_calendars"] = sorted(set(hidden_calendars))
        return public_account(a)


def remove_account(key: str) -> None:
    with _edit() as data:
        a = _find(data["accounts"], key)
        data["accounts"] = [x for x in data["accounts"] if x is not a]
    token = a.get("refresh_token")
    if token:  # best effort: also revoke at Google
        with contextlib.suppress(Exception):
            _http("POST", REVOKE_URL, form={"token": token}, timeout=10)


# ---------------------------------------------------------------- http

def _http(method: str, url: str, *, params: dict | None = None, form: dict | None = None,
          body: Any = None, token: str | None = None, timeout: int = 20) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)}"
    headers = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}")
        except ValueError:
            detail = {}
        err = detail.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or str(err.get("status"))
        else:
            msg = detail.get("error_description") or err or e.reason
        raise CalendarError(f"Google returned {e.code}: {msg}", status=e.code if e.code < 500 else 502)
    except urllib.error.URLError as e:
        raise CalendarError(f"Could not reach Google: {e.reason}", status=502)


# ---------------------------------------------------------------- oauth

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def start_sign_in() -> dict:
    """Begin adding an account. Returns the Google URL to open."""
    cfg = client_config()
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    with _edit() as data:
        now = time.time()
        data["pending"] = {k: v for k, v in data["pending"].items() if v.get("expires", 0) > now}
        data["pending"][state] = {"verifier": verifier, "expires": now + PENDING_TTL}
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return {"auth_url": url, "state": state, "redirect_uri": cfg["redirect_uri"]}


def parse_redirect(pasted: str) -> tuple[str, str | None]:
    """Pull (code, state) out of the URL the browser landed on (or a bare code)."""
    text = (pasted or "").strip()
    if not text:
        raise CalendarError("Paste the full address from the browser after signing in.")
    if "code=" not in text and "error=" not in text:
        return text, None  # a bare code
    query = urllib.parse.urlparse(text).query or text.split("?", 1)[-1]
    q = urllib.parse.parse_qs(query)
    if "error" in q:
        raise CalendarError(f"Google sign-in was not completed: {q['error'][0]}")
    code = (q.get("code") or [""])[0]
    if not code:
        raise CalendarError("That address has no sign-in code in it. Copy the whole address bar.")
    return code, (q.get("state") or [None])[0]


def finish_sign_in(pasted: str, state: str | None = None) -> dict:
    """Exchange the pasted redirect for tokens and save the account."""
    cfg = client_config()
    code, url_state = parse_redirect(pasted)
    state = url_state or state
    with _edit() as data:
        pending = data["pending"].pop(state or "", None) if state else None
        if pending is None and not state and len(data["pending"]) == 1:
            state, pending = data["pending"].popitem()  # bare code + one sign-in in flight
    if not pending or pending.get("expires", 0) < time.time():
        raise CalendarError("This sign-in expired or was already used. Click Add account to start again.")
    tok = _http("POST", TOKEN_URL, form={
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "code": code,
        "code_verifier": pending["verifier"],
        "grant_type": "authorization_code",
        "redirect_uri": cfg["redirect_uri"],
    })
    granted = set((tok.get("scope") or "").split())
    if not set(SCOPES) <= granted:
        raise CalendarError("Calendar access wasn't granted. Sign in again and tick the calendar permission.")
    if not tok.get("refresh_token"):
        raise CalendarError("Google didn't return a refresh token. Remove the app's access at "
                            "myaccount.google.com/permissions and add the account again.")
    primary = _http("GET", f"{API}/users/me/calendarList/primary", token=tok["access_token"])
    email = primary["id"]
    with _edit() as data:
        existing = next((a for a in data["accounts"] if a["email"].lower() == email.lower()), None)
        if existing is None:
            existing = {
                "id": email.lower(),
                "email": email,
                "label": email,
                "color": ACCOUNT_COLORS[len(data["accounts"]) % len(ACCOUNT_COLORS)],
                "hidden_calendars": [],
                "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            data["accounts"].append(existing)
        existing.update({
            "refresh_token": tok["refresh_token"],
            "access_token": tok["access_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 3600)),
        })
        _calendar_cache.pop(existing["id"], None)
        return public_account(existing)


def _access_token(account_id: str) -> str:
    with _edit() as data:
        a = _find(data["accounts"], account_id)
        if a.get("access_token") and a.get("expires_at", 0) - 120 > time.time():
            return a["access_token"]
        cfg = client_config()
        try:
            tok = _http("POST", TOKEN_URL, form={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "refresh_token": a["refresh_token"],
                "grant_type": "refresh_token",
            })
        except CalendarError as e:
            if "invalid_grant" in str(e) or e.status in (400, 401):
                raise CalendarError(
                    f"{a.get('label') or a['email']} needs to sign in again (Google expired or revoked "
                    "its access). Remove it and add it again; see the README about Testing vs In production.",
                    status=401,
                )
            raise
        a["access_token"] = tok["access_token"]
        a["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
        if tok.get("refresh_token"):
            a["refresh_token"] = tok["refresh_token"]
        return a["access_token"]


def _api(account_id: str, method: str, path: str, **kw) -> Any:
    return _http(method, f"{API}{path}", token=_access_token(account_id), **kw)


# ---------------------------------------------------------------- calendars

_calendar_cache: dict[str, tuple[float, list[dict]]] = {}


def _account_calendars(account: dict, refresh: bool = False) -> list[dict]:
    hit = _calendar_cache.get(account["id"])
    if hit and not refresh and hit[0] > time.time():
        cals = hit[1]
    else:
        items, page = [], None
        while True:
            res = _api(account["id"], "GET", "/users/me/calendarList", params={"pageToken": page, "maxResults": 250})
            items += res.get("items", [])
            page = res.get("nextPageToken")
            if not page:
                break
        cals = [{
            "id": c["id"],
            "name": c.get("summaryOverride") or c.get("summary") or c["id"],
            "color": c.get("backgroundColor"),
            "primary": bool(c.get("primary")),
            "access": c.get("accessRole"),
            "writable": c.get("accessRole") in ("owner", "writer"),
            "time_zone": c.get("timeZone"),
        } for c in items if not c.get("deleted")]
        cals.sort(key=lambda c: (not c["primary"], c["name"].lower()))
        _calendar_cache[account["id"]] = (time.time() + CALENDAR_CACHE_TTL, cals)
    hidden = set(account.get("hidden_calendars", []))
    return [{**c, "visible": c["id"] not in hidden, "account_id": account["id"]} for c in cals]


def list_calendars(refresh: bool = False) -> list[dict]:
    """Every connected account with its calendars. Per-account errors are reported, not raised."""
    out = []
    for a in list_accounts():
        entry = dict(a)
        try:
            entry["calendars"] = _account_calendars(a, refresh)
        except CalendarError as e:
            entry["calendars"], entry["error"] = [], str(e)
        out.append(entry)
    return out


# ---------------------------------------------------------------- events

def _normalize(ev: dict, account: dict, cal: dict) -> dict:
    start, end = ev.get("start", {}), ev.get("end", {})
    all_day = "date" in start
    return {
        "id": ev["id"],
        "account_id": account["id"],
        "account_label": account.get("label") or account["email"],
        "calendar_id": cal["id"],
        "calendar_name": cal["name"],
        "color": cal.get("color") or account.get("color"),
        "writable": cal.get("writable", False),
        "summary": ev.get("summary") or "(no title)",
        "description": ev.get("description") or "",
        "location": ev.get("location") or "",
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": all_day,
        "time_zone": start.get("timeZone"),
        "recurring": bool(ev.get("recurringEventId")),
        "status": ev.get("status"),
        "html_link": ev.get("htmlLink"),
        "meet_link": ev.get("hangoutLink") or next(
            (e.get("uri") for e in (ev.get("conferenceData") or {}).get("entryPoints", []) if e.get("entryPointType") == "video"), None),
        "attendees": [{"email": x.get("email"), "name": x.get("displayName"), "response": x.get("responseStatus"),
                       "self": bool(x.get("self"))} for x in ev.get("attendees", [])],
        "organizer": (ev.get("organizer") or {}).get("email"),
    }


def _sort_key(e: dict) -> tuple:
    return (e["start"][:10], 0 if e["all_day"] else 1, e["start"], e["summary"].lower())


def list_events(time_min: str, time_max: str, account: str | None = None, calendar_id: str | None = None,
                query: str | None = None, include_hidden: bool = False, limit: int = 500) -> dict:
    """Merged events from every visible calendar of every account, sorted by start.

    time_min / time_max: RFC3339 timestamps (with offset) or YYYY-MM-DD dates.
    Returns {"events": [...], "errors": [...]} so one broken account never hides the rest.
    """
    def rfc(v: str, end: bool = False) -> str:
        v = v.strip()
        if len(v) == 10:  # a bare date
            return f"{v}T{'23:59:59' if end else '00:00:00'}Z"
        return v

    tmin, tmax = rfc(time_min), rfc(time_max, end=True)
    jobs = []
    errors = []
    with _file_lock():
        accounts = _read()["accounts"]
    if account:
        accounts = [_find(accounts, account)]
    for a in accounts:
        try:
            cals = _account_calendars(a)
        except CalendarError as e:
            errors.append({"account": a.get("label") or a["email"], "error": str(e)})
            continue
        for c in cals:
            if calendar_id and c["id"] != calendar_id:
                continue
            if not (include_hidden or calendar_id or c["visible"]):
                continue
            jobs.append((a, c))

    def fetch(job):
        a, c = job
        items, page = [], None
        while True:
            res = _api(a["id"], "GET", f"/calendars/{urllib.parse.quote(c['id'], safe='')}/events", params={
                "timeMin": tmin, "timeMax": tmax, "singleEvents": "true", "orderBy": "startTime",
                "maxResults": 250, "pageToken": page, "q": query or None,
            })
            items += res.get("items", [])
            page = res.get("nextPageToken")
            if not page or len(items) >= limit:
                break
        return [_normalize(ev, a, c) for ev in items if ev.get("status") != "cancelled"]

    events = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for job, fut in [(j, pool.submit(fetch, j)) for j in jobs]:
            try:
                events += fut.result()
            except CalendarError as e:
                errors.append({"account": job[0].get("label") or job[0]["email"], "calendar": job[1]["name"], "error": str(e)})
    events.sort(key=_sort_key)
    return {"events": events[:limit], "errors": errors}


def _event_body(fields: dict, time_zone: str | None) -> dict:
    body: dict = {}
    for k in ("summary", "description", "location"):
        if fields.get(k) is not None:
            body[k] = fields[k]
    start, end = fields.get("start"), fields.get("end")
    all_day = fields.get("all_day")
    if start or end:
        if all_day is None:
            all_day = bool(start and len(start) == 10)
        if all_day:
            if not (start and end):
                raise CalendarError("All-day events need both a start and an end date (end is exclusive).")
            body["start"], body["end"] = {"date": start[:10]}, {"date": end[:10]}
        else:
            if not (start and end):
                raise CalendarError("Timed events need both a start and an end.")
            tz = fields.get("time_zone") or time_zone
            body["start"] = {"dateTime": start, **({"timeZone": tz} if tz else {})}
            body["end"] = {"dateTime": end, **({"timeZone": tz} if tz else {})}
    if fields.get("attendees") is not None:
        body["attendees"] = [{"email": e} for e in fields["attendees"] if e]
    return body


def _calendar(account: dict, calendar_id: str) -> dict:
    cals = _account_calendars(account)
    if calendar_id in ("", "primary", None):
        cal = next((c for c in cals if c["primary"]), None)
    else:
        cal = next((c for c in cals if c["id"] == calendar_id or c["name"].lower() == calendar_id.lower()), None)
    if cal is None:
        raise CalendarError(f"Calendar '{calendar_id}' not found on {account.get('label') or account['email']}.", status=404)
    return cal


def _account(key: str) -> dict:
    with _file_lock():
        return _find(_read()["accounts"], key)


def create_event(account: str, fields: dict, calendar_id: str = "primary", send_updates: str = "none") -> dict:
    a = _account(account)
    cal = _calendar(a, calendar_id)
    if not cal["writable"]:
        raise CalendarError(f"You can't add events to '{cal['name']}' (read-only).", status=403)
    body = _event_body(fields, cal.get("time_zone"))
    if "start" not in body:
        raise CalendarError("A new event needs a start and an end.")
    body.setdefault("summary", "(no title)")
    ev = _api(a["id"], "POST", f"/calendars/{urllib.parse.quote(cal['id'], safe='')}/events",
              params={"sendUpdates": send_updates}, body=body)
    return _normalize(ev, a, cal)


def update_event(account: str, calendar_id: str, event_id: str, fields: dict, send_updates: str = "none") -> dict:
    """Patch one event. For a recurring event this changes only that occurrence."""
    a = _account(account)
    cal = _calendar(a, calendar_id)
    if not cal["writable"]:
        raise CalendarError(f"'{cal['name']}' is read-only.", status=403)
    body = _event_body(fields, cal.get("time_zone"))
    if not body:
        raise CalendarError("Nothing to change.")
    ev = _api(a["id"], "PATCH",
              f"/calendars/{urllib.parse.quote(cal['id'], safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
              params={"sendUpdates": send_updates}, body=body)
    return _normalize(ev, a, cal)


def move_event(account: str, calendar_id: str, event_id: str, destination_calendar_id: str) -> dict:
    a = _account(account)
    src, dst = _calendar(a, calendar_id), _calendar(a, destination_calendar_id)
    ev = _api(a["id"], "POST",
              f"/calendars/{urllib.parse.quote(src['id'], safe='')}/events/{urllib.parse.quote(event_id, safe='')}/move",
              params={"destination": dst["id"]})
    return _normalize(ev, a, dst)


def delete_event(account: str, calendar_id: str, event_id: str, send_updates: str = "none") -> None:
    """Delete one event. For a recurring event this removes only that occurrence."""
    a = _account(account)
    cal = _calendar(a, calendar_id)
    if not cal["writable"]:
        raise CalendarError(f"'{cal['name']}' is read-only.", status=403)
    _api(a["id"], "DELETE",
         f"/calendars/{urllib.parse.quote(cal['id'], safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
         params={"sendUpdates": send_updates})


def free_busy(time_min: str, time_max: str, account: str | None = None) -> list[dict]:
    """Busy blocks per account across its visible calendars."""
    out = []
    with _file_lock():
        accounts = _read()["accounts"]
    if account:
        accounts = [_find(accounts, account)]
    for a in accounts:
        try:
            ids = [c["id"] for c in _account_calendars(a) if c["visible"]]
            res = _api(a["id"], "POST", "/freeBusy", body={"timeMin": time_min, "timeMax": time_max,
                                                          "items": [{"id": i} for i in ids]})
            busy = sorted((b for c in res.get("calendars", {}).values() for b in c.get("busy", [])),
                          key=lambda b: b["start"])
            out.append({"account": a.get("label") or a["email"], "busy": busy})
        except CalendarError as e:
            out.append({"account": a.get("label") or a["email"], "error": str(e)})
    return out


# ================================================================ reminders + briefings

DEFAULTS = {
    "reminders_enabled": True,
    "reminder_minutes": "10",
    "reminders_desktop": True,
    "reminder_chat_target": "",
    "reminder_skip_declined": True,
    "reminder_skip_all_day": True,
    "reminder_quiet_hours": "",
    "briefing_greeting_enabled": True,
    "briefing_phrases": "good morning, morning, gm",
    "briefing_once_per_day": True,
    "briefing_include_tomorrow": False,
    "briefing_schedule_enabled": False,
    "briefing_time": "07:30",
    "briefing_days": "weekdays",
    "briefing_deliver": "",
    "time_zone": "",
}

JOB_PREFIX = "Google Calendars: "
REMINDER_JOB = JOB_PREFIX + "event reminders"
BRIEFING_JOB = JOB_PREFIX + "daily briefing"
REMINDER_SCRIPT = "google-calendars-reminders.py"


def plugin_version() -> str:
    """The installed agent half's version (plugin.yaml), so the panel can spot a stale copy of itself."""
    try:
        for line in (Path(__file__).resolve().parent / "plugin.yaml").read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def setting(key: str):
    v = _plugin_settings().get(key)
    if v is None or (v == "" and isinstance(DEFAULTS[key], bool)):
        return DEFAULTS[key]
    return v


def _as_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def lead_minutes() -> list[int]:
    out = set()
    for part in str(setting("reminder_minutes") or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() and 0 < int(part) <= 7 * 24 * 60:
            out.add(int(part))
    return sorted(out, reverse=True) or [10]


def _tz():
    from datetime import datetime
    name = (setting("time_zone") or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo


def _now():
    from datetime import datetime
    return datetime.now(_tz())


def _parse_when(v: str):
    from datetime import datetime
    if len(v) == 10:
        return datetime.fromisoformat(v).replace(tzinfo=_tz())
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(_tz())


def in_quiet_hours(now) -> bool:
    spec = (setting("reminder_quiet_hours") or "").strip()
    if "-" not in spec:
        return False
    try:
        a, b = [x.strip() for x in spec.split("-", 1)]
        start = int(a[:2]) * 60 + int(a[3:5])
        end = int(b[:2]) * 60 + int(b[3:5])
    except (ValueError, IndexError):
        return False
    m = now.hour * 60 + now.minute
    return start <= m < end if start <= end else (m >= start or m < end)


def _declined(ev: dict) -> bool:
    return any(a.get("self") and a.get("response") == "declined" for a in ev.get("attendees", []))


_SENT_FILE = "reminders_sent.json"


def _sent_path() -> Path:
    return data_dir() / _SENT_FILE


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _fmt_time(dt) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def due_reminders(consumer: str, now=None) -> list[dict]:
    """Reminders due right now for one channel ("desktop" or "chat"), each sent once.

    An event is due for lead L when start - L <= now < start. If several leads are
    due at once (the app was closed), only one reminder goes out for that event.
    """
    from datetime import timedelta
    if not _as_bool(setting("reminders_enabled")):
        return []
    if not is_configured() or not list_accounts():
        return []
    now = now or _now()
    leads = lead_minutes()
    res = list_events(now.isoformat(), (now + timedelta(minutes=max(leads) + 1)).isoformat())
    skip_all_day = _as_bool(setting("reminder_skip_all_day"))
    skip_declined = _as_bool(setting("reminder_skip_declined"))
    quiet = in_quiet_hours(now)
    out = []
    with _file_lock():
        sent = _load_json(_sent_path())
        cutoff = time.time() - 2 * 86400
        sent = {k: v for k, v in sent.items() if v > cutoff}
        for ev in res["events"]:
            if (ev["all_day"] and skip_all_day) or (skip_declined and _declined(ev)):
                continue
            start = _parse_when(ev["start"])
            if start <= now:
                continue
            due = [L for L in leads if start - timedelta(minutes=L) <= now]
            fresh = [L for L in due if f"{consumer}|{ev['account_id']}|{ev['id']}|{ev['start']}|{L}" not in sent]
            if not fresh:
                continue
            for L in due:
                sent[f"{consumer}|{ev['account_id']}|{ev['id']}|{ev['start']}|{L}"] = start.timestamp()
            if quiet:
                continue
            mins = max(1, round((start - now).total_seconds() / 60))
            out.append({
                "id": ev["id"], "account_id": ev["account_id"], "calendar_id": ev["calendar_id"],
                "summary": ev["summary"], "account_label": ev["account_label"],
                "start": ev["start"], "minutes": mins, "time": _fmt_time(start),
                "location": ev["location"], "meet_link": ev.get("meet_link"), "html_link": ev.get("html_link"),
            })
        _save_json(_sent_path(), sent)
    return out


def reminder_text(reminders: list[dict]) -> str:
    lines = []
    for r in reminders:
        line = f"⏰ In {r['minutes']} min ({r['time']}): {r['summary']} · {r['account_label']}"
        if r.get("meet_link"):
            line += f"\n   Join: {r['meet_link']}"
        elif r.get("location"):
            line += f"\n   Where: {r['location']}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- greeting briefing

def _phrases() -> list[str]:
    return [p.strip().lower() for p in str(setting("briefing_phrases") or "").split(",") if p.strip()]


def is_greeting(message: str) -> bool:
    import re
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    for p in sorted(_phrases(), key=len, reverse=True):
        if text == p or re.match(re.escape(p) + r"(?![a-z0-9])", text):
            return True
    return False


def agenda_text(day_offset_list=(0,), now=None) -> str:
    from datetime import timedelta
    now = now or _now()
    blocks = []
    for off in day_offset_list:
        day = (now + timedelta(days=off)).replace(hour=0, minute=0, second=0, microsecond=0)
        res = list_events(day.isoformat(), (day + timedelta(days=1)).isoformat())
        label = "Today" if off == 0 else "Tomorrow" if off == 1 else day.strftime("%A")
        lines = [f"{label} ({day.strftime('%A, %B %d').replace(' 0', ' ')}):"]
        evs = [e for e in res["events"] if not _declined(e)]
        if not evs:
            lines.append("  (nothing scheduled)")
        for e in evs:
            if e["all_day"]:
                when = "all day"
            else:
                when = f"{_fmt_time(_parse_when(e['start']))}-{_fmt_time(_parse_when(e['end']))}"
            extra = " [video call]" if e.get("meet_link") else (f" @ {e['location']}" if e["location"] else "")
            lines.append(f"  - {when}: {e['summary']} ({e['account_label']}){extra}")
        for err in res["errors"]:
            lines.append(f"  ! could not read {err['account']}: {err['error']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_BRIEFED_FILE = "briefed.json"


def greeting_context(message: str, now=None) -> str | None:
    """Context for the pre_llm_call hook, or None when this message shouldn't trigger a briefing."""
    if not _as_bool(setting("briefing_greeting_enabled")) or not is_greeting(message):
        return None
    if not is_configured() or not list_accounts():
        return None
    now = now or _now()
    today = now.strftime("%Y-%m-%d")
    if _as_bool(setting("briefing_once_per_day")):
        with _file_lock():
            path = data_dir() / _BRIEFED_FILE
            state = _load_json(path)
            if state.get("last") == today:
                return None
            state["last"] = today
            _save_json(path, state)
    days = (0, 1) if _as_bool(setting("briefing_include_tomorrow")) else (0,)
    return (
        "[Google Calendars plugin] The user just greeted you. Open your reply with a short, friendly rundown of "
        "their calendar from the data below: what's first, the shape of the day, any back-to-back or overlapping "
        "meetings across accounts, and free stretches. Keep it brief, then answer anything else they said.\n\n"
        + agenda_text(days, now)
    )


# ---------------------------------------------------------------- scheduled jobs (Hermes cron)

def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _write_reminder_script() -> str:
    scripts = _hermes_home() / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    plugin_dir = str(Path(__file__).resolve().parent)
    body = (
        "# Written by the google-calendars plugin: prints calendar reminders that are due for chat\n"
        "# delivery (empty output = nothing due). Managed automatically; edits are overwritten.\n"
        "import os, sys\n"
        f"os.environ.setdefault('HERMES_HOME', {str(_hermes_home())!r})\n"
        f"sys.path.insert(0, {plugin_dir!r})\n"
        "import google_calendars_core as c\n"
        "print(c.reminder_text(c.due_reminders('chat')))\n"
    )
    path = scripts / REMINDER_SCRIPT
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        path.write_text(body, encoding="utf-8")
    return REMINDER_SCRIPT


def _ensure_secret_passthrough() -> None:
    """Cron scripts get a sanitized environment; Hermes's documented way to hand one a
    credential is terminal.env_passthrough. Needed so the reminder script can refresh tokens."""
    from hermes_cli.config import load_config, save_config
    cfg = load_config()
    term = cfg.setdefault("terminal", {})
    lst = list(term.get("env_passthrough") or [])
    if SECRET_ENV not in lst:
        lst.append(SECRET_ENV)
        term["env_passthrough"] = lst
        save_config(cfg)


def _briefing_schedule() -> str:
    t = str(setting("briefing_time") or "07:30").strip()
    try:
        hh, mm = int(t[:2]), int(t[3:5])
    except ValueError:
        hh, mm = 7, 30
    days = {"weekdays": "1-5", "every day": "*", "everyday": "*", "daily": "*", "weekends": "0,6"}.get(
        str(setting("briefing_days") or "weekdays").strip().lower(), "1-5")
    return f"{mm} {hh} * * {days}"


BRIEFING_PROMPT = (
    "Give me my calendar briefing for today. Use google_calendar_events for today across all my connected "
    "accounts (and google_calendar_free_busy if useful). Lead with the first event, then the shape of the day: "
    "meetings in order with times and which account, back-to-back or overlapping meetings, and free stretches. "
    "Short and skimmable; no preamble."
)


def sync_jobs() -> dict:
    """Make Hermes's scheduled jobs match the settings: create, update or remove the
    reminder job (no-agent, zero tokens) and the daily briefing job. Idempotent."""
    from cron import jobs as cj
    existing = {j.get("name"): j for j in cj.list_jobs(include_disabled=True) if str(j.get("name", "")).startswith(JOB_PREFIX)}
    result = {}

    target = str(setting("reminder_chat_target") or "").strip()
    want_reminders = bool(target) and _as_bool(setting("reminders_enabled"))
    job = existing.get(REMINDER_JOB)
    if want_reminders:
        script = _write_reminder_script()
        _ensure_secret_passthrough()
        if job is None:
            cj.create_job(None, "every 1m", name=REMINDER_JOB, deliver=target, script=script, no_agent=True,
                          grace_minutes=0)
            result["reminders"] = "created"
        elif job.get("deliver") != target:
            cj.update_job(job["id"], {"deliver": target})
            result["reminders"] = "updated"
        else:
            result["reminders"] = "ok"
    elif job is not None:
        cj.remove_job(job["id"])
        result["reminders"] = "removed"

    want_briefing = _as_bool(setting("briefing_schedule_enabled"))
    deliver = str(setting("briefing_deliver") or "").strip() or None
    schedule = _briefing_schedule()
    job = existing.get(BRIEFING_JOB)
    if want_briefing:
        tz = (setting("time_zone") or "").strip() or None
        if job is None:
            cj.create_job(BRIEFING_PROMPT, schedule, name=BRIEFING_JOB, deliver=deliver, timezone=tz,
                          enabled_toolsets=["google_calendars"])
            result["briefing"] = "created"
        elif (job.get("schedule") or {}).get("expr") != schedule or (job.get("deliver") or None) != deliver:
            cj.update_job(job["id"], {"schedule": schedule, "deliver": deliver})
            result["briefing"] = "updated"
        else:
            result["briefing"] = "ok"
    elif job is not None:
        cj.remove_job(job["id"])
        result["briefing"] = "removed"
    return result
