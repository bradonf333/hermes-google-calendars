# Privacy policy: Google Calendars for Hermes

*Last updated: November 23, 2026*

Google Calendars for Hermes ("the plugin") is open-source software that you
install and run yourself, inside your own [Hermes](https://github.com/NousResearch/hermes-agent)
setup, with a Google OAuth client you create in your own Google Cloud project.

**There is no service behind it.** The plugin's author does not operate a
server, receive your data, or have access to your Google account or calendars.

## What the plugin accesses

- With your permission, the Google Calendar data of each Google account you
  connect (scope `https://www.googleapis.com/auth/calendar`): your calendar
  list and events, so it can show, create, edit and delete them for you.
- Nothing else: no Gmail, Drive, contacts or profile data.

## Where your data goes

- Calendar data is fetched directly from Google's APIs (`www.googleapis.com`)
  by the Hermes gateway running on your own machine or server, and shown in
  your Hermes Desktop app.
- If you use the plugin's agent tools, the calendar details the agent looks up
  are sent to whichever AI model provider you have configured in Hermes, as
  part of your own conversation. That is governed by your agreement with that
  provider.
- Nothing is sent to the plugin's author or any other third party.

## What is stored

- OAuth tokens for each connected account, in
  `~/.hermes/plugin-data/google-calendars/accounts.json` on the machine running
  your Hermes gateway, readable only by your user account.
- Your display preferences (account names and colors, hidden calendars).
- Which reminders were already shown (event IDs and times, kept two days) and
  the date of your last greeting briefing, so nothing repeats.
- If you turn on chat reminders or a scheduled briefing, Hermes scheduled jobs
  that send them to a chat app you already connected to Hermes. Reminder text
  (event title, time, link) then goes through that chat service.
- Calendar events are not stored; they are fetched when you view them.

## Removing access

- **Disconnect** an account in the plugin: its tokens are deleted and revoked
  at Google.
- Or remove access at any time at <https://myaccount.google.com/permissions>.
- Uninstalling the plugin and deleting `~/.hermes/plugin-data/google-calendars/`
  removes everything it stored.

The plugin's use of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Contact

Questions: open an issue at <https://github.com/bradonf333/hermes-google-calendars/issues>.
