# Google Calendars for Hermes

Every Google Calendar from **every Google account** you use, merged into one
docked panel in [Hermes](https://github.com/NousResearch/hermes-agent) Desktop,
plus calendar tools for the agent.

- **One calendar, many accounts.** Connect as many Google accounts as you like
  (work, side job, personal). Their calendars are merged and color-coded.
- **A docked panel.** Opens on the right side like the Files pane: drag it
  anywhere, collapse it to a tab, open it again.
- **Day, Week and Agenda views.** Overlapping events sit side by side, all-day
  events get their own row, and a line marks the current time.
- **Manage events.** Click an event for details, the video link and guests.
  Edit or delete it, or create one on any calendar you can write to.
  Double-click an empty slot to start a new event there.
- **Show or hide calendars** per account (the choice is remembered).
- **Agent tools**, so you can ask things like *"what's on my calendar
  tomorrow?"*, *"when am I free Thursday across all my calendars?"* or
  *"put a 30 minute review on my work calendar at 2pm Tuesday"*.

You bring your own Google login client (free, about 5 minutes to set up), so
your calendar data never passes through anyone else's app or server.

## Install

```bash
hermes plugins install bradonf333/hermes-google-calendars --enable
```

Or in Hermes Desktop: **Capabilities → Plugins → Install from Git** with
`bradonf333/hermes-google-calendars`.

Then:

1. Make sure `google-calendars` is under `plugins.enabled` in
   `~/.hermes/config.yaml` (the `--enable` flag does this). The panel's
   backend only loads when it is.
2. Restart the gateway (`hermes gateway restart`, or from the desktop app).
3. In Desktop, turn on the plugin's desktop half under
   **Capabilities → Plugins** (desktop halves ship switched off).

The **Calendar** pane appears on the right. Until you finish the Google
setup below it shows the setup steps.

## Google setup (one time, about 5 minutes)

Do this with any Google account; a personal Gmail is a good choice because it
doesn't depend on any employer. **The account that owns the project does not
limit which accounts you can connect.** Every account signs in on its own.

1. **Create a project.** Go to <https://console.cloud.google.com/> and click
   **New project** (any name, e.g. `Hermes Calendars`).
2. **Turn on the Calendar API.** Go to **APIs & Services → Library**, search
   for *Google Calendar API*, and click **Enable**.
3. **Set up the consent screen.** Under **APIs & Services → OAuth consent
   screen** (in newer consoles, **Google Auth Platform → Branding /
   Audience**):
   - User type **External**.
   - App name, your email for support and developer contact. Nothing else
     is required.
   - **Data access / Scopes:** you can skip this. The plugin asks for the
     calendar scope itself.
   - **Audience → Publish app**, so the status is **In production**. This
     matters: while an app is in *Testing*, Google expires its sign-ins after
     **7 days**. Publishing doesn't need Google's review for personal use; you
     just click through an "unverified app" warning once per account.
4. **Create the login client.** Under **Credentials → Create credentials →
   OAuth client ID**, choose application type **Desktop app**. Copy the
   **Client ID** and **Client secret**.
5. **Paste them into Hermes.** In Desktop go to **Settings → Plugins → Google
   Calendars**, fill in *OAuth Client ID* and *OAuth Client secret*, and save.
   The secret is stored in `~/.hermes/.env`, never in `config.yaml`.
   Without Desktop:

   ```bash
   hermes config set plugins.entries.google-calendars.settings.client_id "<id>.apps.googleusercontent.com"
   echo 'GOOGLE_CALENDARS_CLIENT_SECRET=<secret>' >> ~/.hermes/.env
   ```

## Connecting accounts

In the panel click the **account** icon, then **Add account**:

1. **Open Google sign-in** opens the browser. Pick the account and allow
   calendar access. If you see *"Google hasn't verified this app"*, click
   **Advanced → Go to (your app name)**. It's your own app.
2. The browser lands on `http://127.0.0.1:8765/?state=...&code=...` and
   shows *"This site can't be reached"*. **That's expected.** Copy the whole
   address from the address bar, paste it into the panel, and click
   **Connect**.

Repeat for every account. Rename an account (e.g. *Work*), change its color, or
disconnect it from the same screen. Disconnecting also revokes the access at
Google.

Pasting the address back (instead of the usual "redirect to a local page")
is what makes sign-in work whether Hermes runs on this computer, inside WSL,
or on a remote server.

### Work or school accounts (Google Workspace)

A Workspace admin can block apps their organization hasn't approved. If
signing in with a work account says the app is blocked (*"access blocked"*,
*admin_policy_enforced*), ask your admin to allow the app under **Admin
console → Security → Access and data control → API controls → Manage
Third-Party App Access**, using your Client ID. Some organizations also
restrict which calendars can be shared outside the domain; this plugin signs
in to each account directly, so sharing settings don't matter.

## Agent tools

Once at least one account is connected, the agent gets these tools (toolset
`google_calendars`):

| Tool | What it does |
|---|---|
| `google_calendar_calendars` | Connected accounts and their calendars (ids, read-only or not, shown or hidden in the panel) |
| `google_calendar_events` | Events across every account, merged and sorted; filter by account, calendar or text |
| `google_calendar_free_busy` | Busy blocks per account, for finding open time across everything |
| `google_calendar_create_event` | Create an event on a chosen account and calendar (optionally invite guests) |
| `google_calendar_update_event` | Change an event, or move it to another calendar of the same account |
| `google_calendar_delete_event` | Delete an event |

Edits and deletes of a recurring event apply to **that one occurrence**.
Guests are only emailed when the agent explicitly asks to (`send_updates`).

## Privacy and security

- **Scope:** `https://www.googleapis.com/auth/calendar` (read and write your
  calendars). Nothing else: no Gmail, Drive or contacts.
- **Where tokens live:** `~/.hermes/plugin-data/google-calendars/accounts.json`
  on the machine running the Hermes gateway, owner-only (`0600`, folder
  `0700`). Tokens never go to the desktop UI, the model, or this repo.
- **Network:** the plugin only talks to `accounts.google.com`,
  `oauth2.googleapis.com` and `www.googleapis.com`.
- **Disconnect** removes the account's tokens and revokes them at Google. You
  can also remove access any time at <https://myaccount.google.com/permissions>.

## Settings

| Setting | Default | |
|---|---|---|
| OAuth Client ID | | From step 4 above |
| OAuth Client secret | | Stored as `GOOGLE_CALENDARS_CLIENT_SECRET` in `.env` |
| Redirect address | `http://127.0.0.1:8765/` | Change only if you need a different port. Desktop clients accept any `127.0.0.1` port |

## Troubleshooting

| Problem | Fix |
|---|---|
| Panel says *Backend unavailable* | `google-calendars` isn't in `plugins.enabled`, or the gateway wasn't restarted after installing |
| No Calendar pane | Turn on the desktop half under **Capabilities → Plugins**. If you closed the pane, use **Reset layout** |
| *"needs to sign in again"* after about a week | The consent screen is still in **Testing**. Publish it (step 3), then remove the account and add it again |
| *"didn't return a refresh token"* | Remove the app at myaccount.google.com/permissions, then add the account again |
| *redirect_uri_mismatch* | The OAuth client isn't a **Desktop app** client. Create one of that type |
| A work account is blocked | See [Work or school accounts](#work-or-school-accounts-google-workspace) |

## Development

```
plugin.yaml                  manifest: settings form + tool list
__init__.py                  agent tools
google_calendars_core.py     shared core: settings, token store, OAuth, Calendar API (stdlib only)
dashboard/plugin_api.py      panel backend → /api/plugins/google-calendars
desktop/plugin.js            the panel (plain ESM, no build step)
tests/                       pytest (core, tools, routes) + a jsdom UI test
```

```bash
pip install pytest fastapi httpx
pytest -q                                  # core, tools and backend routes (Google is faked)
npm install --no-save react@19 react-dom@19 jsdom
node tests/ui_smoke.mjs node_modules       # renders the panel in jsdom and drives it
python tests/mutate.py node_modules        # breaks key behaviours one at a time; every one must be caught
hermes plugins validate .
```

To work on it live, link the checkout into your plugins folder; Desktop
re-syncs `desktop/plugin.js` on every save:

```bash
ln -s "$PWD" ~/.hermes/plugins/google-calendars
```

## License

MIT
