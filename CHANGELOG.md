# Changelog

All notable changes to Google Calendars for Hermes. Versions follow
[semantic versioning](https://semver.org/); the newest is on top. The same
notes appear in the panel's one-time "What's new" banner after an update.

## 0.3.2 (2026-11-23)

- **Greeting briefings are additive.** The calendar rundown is added to
  whatever the agent normally says when greeted (its usual catch-up), instead
  of replacing it.
- **New setting: Also include in briefings.** Plain-words extras for the
  greeting and scheduled briefings, e.g. "new Slack messages, open PRs".

## 0.3.1 (2026-11-23)

- **Stale panel warning.** When Desktop and the Hermes gateway run on different
  computers, Hermes's Update refreshes the gateway's copy of the plugin but not
  the Desktop panel. The panel now compares its version with the gateway's and,
  when it's behind, explains how to update it (Install from Git with Force
  reinstall).

## 0.3.0 (2026-11-23)

- **What's new banner.** After an update the panel shows a one-time note
  with the highlights and a link to this changelog.
- A CHANGELOG and GitHub Releases for every version, plus an "Updating"
  section in the README.

## 0.2.2 (2026-11-23)

- **Duplicate events merged.** The same event on several calendars (same title,
  start and end) shows once, marked ×2; its details list every calendar it's
  on. Toggle it in the show/hide calendars menu.

## 0.2.1 (2026-11-23)

- **Calendar in the sidebar.** A sidebar link and a full-page view (also
  *Open Calendar* in the command palette), so the calendar is reachable
  whatever the layout.

## 0.2.0 (2026-11-23)

- **Event reminders** as a desktop notification and an in-app toast (with
  **Join** for video calls), and optionally in any chat Hermes already
  delivers to (Slack, Telegram, ...), via a script-only scheduled job.
- **Briefings**: say "good morning" (or your own phrase) and Hermes opens its
  reply with your day; optional scheduled daily briefing.
- Settings for lead times, declined and all-day events, quiet hours, phrases,
  briefing time and days, delivery targets and time zone.
- The event details popup is solid instead of see-through.

## 0.1.1 (2026-11-23)

- Text boxes use the app's own components, so pasted and typed text is
  readable in every theme.
- Privacy policy (`PRIVACY.md`) for publishing the Google consent screen.

## 0.1.0 (2026-11-22)

- First release: every Google Calendar from any number of Google accounts in
  one docked panel (Day, Week, Agenda), create/edit/delete events, show/hide
  calendars, and six calendar tools for the agent.
