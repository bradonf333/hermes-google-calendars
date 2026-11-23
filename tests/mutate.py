#!/usr/bin/env python3
"""Mutation check: break one behaviour at a time and confirm a test fails.
Usage: python3 tests/mutate.py <node_modules dir for the UI test>"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NM = sys.argv[1]
PY = [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
UI = ["node", "tests/ui_smoke.mjs", NM]

MUTANTS = [
    ("reminder sent every poll", "google_calendars_core.py",
     "            if not fresh:\n                continue", "            pass", PY),
    ("reminder before its lead time", "google_calendars_core.py",
     "due = [L for L in leads if start - timedelta(minutes=L) <= now]", "due = list(leads)", PY),
    ("desktop + chat share sent state", "google_calendars_core.py",
     'fresh = [L for L in due if f"{consumer}|', 'fresh = [L for L in due if f"x|', PY),
    ("declined events reminded", "google_calendars_core.py",
     "(skip_declined and _declined(ev))", "False", PY),
    ("quiet hours ignored", "google_calendars_core.py",
     "            if quiet:\n                continue", "            pass", PY),
    ("greeting matches inside words", "google_calendars_core.py",
     'r"(?![a-z0-9])"', 'r""', PY),
    ("greeting briefing every message", "google_calendars_core.py",
     'if state.get("last") == today:\n                return None', 'pass', PY),
    ("chat reminder job runs the AI", "google_calendars_core.py",
     "script=script, no_agent=True,", "script=script, no_agent=False,", PY),
    ("briefing job not updated on change", "google_calendars_core.py",
     'cj.update_job(job["id"], {"schedule": schedule, "deliver": deliver})', "pass", PY),
    ("UI: reminders only while pane open", "desktop/plugin.js",
     "    ctx.setInterval(pollReminders, REMINDER_POLL_MS)\n", "", UI),
    ("UI: reminder toast missing", "desktop/plugin.js", "  host.notify({\n    kind: 'info',\n    title: '📅 ' + title,", "  void ({\n    kind: 'info',\n    title: '📅 ' + title,", UI),
    ("UI: no sidebar link", "desktop/plugin.js",
     "      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PAGE, label: 'Calendar', codicon: 'calendar' } },\n", "", UI),
    ("UI: duplicates not merged", "desktop/plugin.js",
     "    if (g.length === 1) return g[0]", "    if (true) return g[0]", UI),
    ("UI: different times merged", "desktop/plugin.js",
     "${(e.summary || '').trim().toLowerCase()}|${e.start}|${e.end}", "${(e.summary || '').trim().toLowerCase()}", UI),
    ("hook not registered", "__init__.py",
     'ctx.register_hook("pre_llm_call", _on_pre_llm_call)', "pass", PY),
    ("sign-in state can be reused", "google_calendars_core.py",
     'pending = data["pending"].pop(state or "", None) if state else None',
     'pending = data["pending"].get(state or "", None) if state else None', PY),
    ("expired sign-in accepted", "google_calendars_core.py",
     'if not pending or pending.get("expires", 0) < time.time():', 'if not pending:', PY),
    ("hidden calendars still fetched", "google_calendars_core.py",
     'if not (include_hidden or calendar_id or c["visible"]):', 'if False:', PY),
    ("read-only calendar accepts new events", "google_calendars_core.py",
     'raise CalendarError(f"You can\'t add events to \'{cal[\'name\']}\' (read-only).", status=403)', 'pass', PY),
    ("tokens leak to the UI", "google_calendars_core.py",
     '"added": a.get("added"),\n    }', '"added": a.get("added"),\n        "refresh_token": a.get("refresh_token"),\n    }', PY),
    ("missing calendar scope accepted", "google_calendars_core.py",
     'if not set(SCOPES) <= granted:', 'if False:', PY),
    ("cancelled events shown", "google_calendars_core.py",
     'if ev.get("status") != "cancelled"]', ']', PY),
    ("events not sorted", "google_calendars_core.py", "events.sort(key=_sort_key)", "pass", PY),
    ("one broken account hides all", "google_calendars_core.py",
     'errors.append({"account": job[0].get("label") or job[0]["email"], "calendar": job[1]["name"], "error": str(e)})',
     "raise", PY),
    ("tools offered with no account", "__init__.py",
     "return core.is_configured() and bool(core.list_accounts())", "return True", PY),
    ("UI: overlapping events stacked", "desktop/plugin.js", "for (const it of g) it.cols = cols.length",
     "for (const it of g) { it.cols = 1; it.col = 0 }", UI),
    ("UI: all-day end not exclusive", "desktop/plugin.js",
     "end: ymd(addDays(parseDate(f.endDate), 1))", "end: f.endDate", UI),
    ("UI: read-only calendars offered", "desktop/plugin.js",
     "a.calendars.filter(c => c.writable)", "a.calendars", UI),
    ("UI: times sent without offset", "desktop/plugin.js",
     "start: localIso(f.date, f.startTime)", "start: `${f.date}T${f.startTime}:00`", UI),
    ("UI: read-only events editable", "desktop/plugin.js",
     "e.writable && jsx(IconButton, { icon: 'edit'", "jsx(IconButton, { icon: 'edit'", UI),
]

killed = 0
for name, rel, old, new, cmd in MUTANTS:
    p = ROOT / rel
    src = p.read_text()
    if src.count(old) != 1:
        print(f"BAD MUTANT ({src.count(old)} matches): {name}")
        continue
    backup = p.with_suffix(p.suffix + ".bak")
    shutil.copy(p, backup)
    try:
        p.write_text(src.replace(old, new))
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
        ok = r.returncode != 0
        killed += ok
        print(f"{'KILLED  ' if ok else 'SURVIVED'} {name}")
    finally:
        shutil.move(backup, p)
for cache in ROOT.rglob("__pycache__"):  # never leave bytecode compiled from a mutant
    shutil.rmtree(cache, ignore_errors=True)
print(f"{killed}/{len(MUTANTS)} killed")
sys.exit(0 if killed == len(MUTANTS) else 1)
