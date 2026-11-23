/**
 * Google Calendars — every Google Calendar from every Google account in one
 * docked panel (right side by default; drag it anywhere, collapse it to a tab).
 *
 * Talks only to its own backend (dashboard/plugin_api.py, /api/plugins/google-calendars)
 * through ctx.rest, so it works the same against a local, WSL or remote gateway.
 *
 * Plain ESM loaded uncompiled: jsx() calls instead of JSX, and only three imports
 * resolve (@hermes/plugin-sdk, react, react/jsx-runtime). Layout uses inline styles
 * plus the app's --ui-* theme variables, because plugin files never reach the app's
 * Tailwind build.
 */

import { Button, Codicon, Input, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, Textarea, host } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'google-calendars'
const HOUR_PX = 44
const REFRESH_MS = 5 * 60 * 1000
const PAGE = '/calendar'
// Keep in step with plugin.yaml `version` and the top CHANGELOG entry (a test checks).
const VERSION = '0.3.0'
const CHANGELOG_URL = 'https://github.com/bradonf333/hermes-google-calendars/blob/main/CHANGELOG.md'
const WHATS_NEW = [
  'Duplicate events (same title and time) show once, marked ×2',
  'Calendar link in the sidebar, opens as a full page',
  'Event reminders and "good morning" briefings (Settings → Plugins → Google Calendars)',
  'This banner: you\u2019ll see what changed after each update'
]
let api = null // plugin ctx, set in register()

// ------------------------------------------------------------------ backend

const qs = params => new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== ''))
const rest = {
  get: (path, params = {}) => api.rest(`${path}?${qs(params)}`),
  post: (path, body) => api.rest(path, { method: 'POST', body }),
  patch: (path, body) => api.rest(path, { method: 'PATCH', body }),
  del: (path, params = {}) => api.rest(`${path}?${qs(params)}`, { method: 'DELETE' })
}
const enc = encodeURIComponent
const errText = e => {
  const s = String(e?.message || e)
  const m = s.match(/"detail"\s*:\s*"((?:[^"\\]|\\.)*)"/)
  return m ? m[1].replace(/\\"/g, '"') : s
}
const fail = e => host.notify({ kind: 'error', message: errText(e) })
const openUrl = url => (api?.os?.openExternal ? api.os.openExternal(url) : Promise.resolve(false)).then(ok => {
  if (!ok) window.open(url, '_blank', 'noopener')
})

// ------------------------------------------------------------------ dates

const pad = n => String(n).padStart(2, '0')
const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const hm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)
const startOfDay = d => new Date(d.getFullYear(), d.getMonth(), d.getDate())
const startOfWeek = (d, weekStart) => addDays(startOfDay(d), -((d.getDay() - weekStart + 7) % 7))
const sameDay = (a, b) => ymd(a) === ymd(b)
const parseDate = s => (s.length === 10 ? new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) : new Date(s))
const fmtTime = d => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
const fmtDay = d => d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
const fmtLongDay = d => d.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })

/** Local wall time -> RFC3339 with the offset that applies on that date. */
function localIso(date, time) {
  const d = new Date(`${date}T${time}:00`)
  const off = -d.getTimezoneOffset()
  const sign = off >= 0 ? '+' : '-'
  return `${date}T${time}:00${sign}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`
}

function eventSpan(e) {
  const start = parseDate(e.start)
  let end = parseDate(e.end)
  if (e.all_day) end = addDays(end, -1) // Google's all-day end date is exclusive
  return { start, end }
}

/** Collapse copies of the same event (same title, same start and end) into one,
 *  e.g. a holiday calendar subscribed on two accounts, or a meeting you're invited
 *  to on both. The kept copy is one you can edit, if any; the rest ride along in
 *  `copies` so the details view can list every calendar it's on. */
function mergeDuplicates(events) {
  const groups = new Map()
  for (const e of events) {
    const key = `${(e.summary || '').trim().toLowerCase()}|${e.start}|${e.end}`
    const g = groups.get(key)
    if (g) g.push(e)
    else groups.set(key, [e])
  }
  return [...groups.values()].map(g => {
    if (g.length === 1) return g[0]
    const main = g.find(x => x.writable) || g[0]
    return { ...main, copies: g.filter(x => x !== main) }
  })
}

const calLabel = x => (x.calendar_name === x.account_label ? x.calendar_name : `${x.calendar_name} · ${x.account_label}`)

function rangeFor(view, anchor, weekStart) {
  if (view === 'day') return { from: startOfDay(anchor), to: addDays(startOfDay(anchor), 1), days: [startOfDay(anchor)] }
  if (view === 'week') {
    const from = startOfWeek(anchor, weekStart)
    return { from, to: addDays(from, 7), days: Array.from({ length: 7 }, (_, i) => addDays(from, i)) }
  }
  const from = startOfDay(anchor)
  return { from, to: addDays(from, 14), days: Array.from({ length: 14 }, (_, i) => addDays(from, i)) }
}

function rangeLabel(view, days) {
  if (view === 'day') return fmtLongDay(days[0])
  const a = days[0]
  const b = days[days.length - 1]
  const sameMonth = a.getMonth() === b.getMonth()
  const left = a.toLocaleDateString([], { month: 'short', day: 'numeric' })
  const right = b.toLocaleDateString([], sameMonth ? { day: 'numeric' } : { month: 'short', day: 'numeric' })
  return `${left} – ${right}, ${b.getFullYear()}`
}

// ------------------------------------------------------------------ small pieces

const S = {
  muted: { color: 'var(--ui-text-tertiary)' },
  faint: { color: 'var(--ui-text-quaternary)' },
  border: '1px solid var(--ui-stroke-secondary)',
  card: {
    background: 'var(--ui-bg-elevated)',
    border: '1px solid var(--ui-stroke-secondary)',
    borderRadius: 8
  },
  input: {
    width: '100%',
    background: 'transparent',
    color: 'inherit',
    colorScheme: 'dark light',
    border: '1px solid var(--ui-stroke-secondary)',
    borderRadius: 6,
    padding: '4px 8px',
    fontSize: 12
  }
}

function IconButton({ icon, label, onClick, disabled, active }) {
  return jsx(Button, {
    variant: 'ghost',
    size: 'xs',
    title: label,
    'aria-label': label,
    disabled,
    onClick,
    style: active ? { background: 'var(--ui-control-active-background)' } : undefined,
    children: jsx(Codicon, { name: icon, size: 14 })
  })
}

function Dot({ color, size = 8 }) {
  return jsx('span', {
    style: { display: 'inline-block', width: size, height: size, borderRadius: '50%', background: color || 'var(--ui-accent)', flexShrink: 0 }
  })
}

function Field({ label, children }) {
  return jsxs('label', {
    style: { display: 'flex', flexDirection: 'column', gap: 3, fontSize: 11 },
    children: [jsx('span', { style: S.muted, children: label }), children]
  })
}

function Seg({ value, options, onChange }) {
  return jsx('div', {
    style: { display: 'flex', border: S.border, borderRadius: 6, overflow: 'hidden' },
    children: options.map(([v, label]) =>
      jsx('button', {
        type: 'button',
        onClick: () => onChange(v),
        style: {
          padding: '2px 8px',
          fontSize: 11,
          background: v === value ? 'var(--ui-control-active-background)' : 'transparent',
          color: v === value ? 'var(--ui-text-primary)' : 'var(--ui-text-tertiary)'
        },
        children: label
      }, v)
    )
  })
}

// ------------------------------------------------------------------ setup + accounts

function SetupScreen({ redirect, onRecheck }) {
  const steps = [
    ['Create a Google Cloud project', 'console.cloud.google.com → New project (any name).'],
    ['Turn on the Calendar API', 'APIs & Services → Library → Google Calendar API → Enable.'],
    ['Set up the consent screen', 'OAuth consent screen → External. On Branding, add a homepage + privacy policy link (the README has ready-made ones), then Audience → Publish app ("In production") so sign-ins don\u2019t expire after 7 days.'],
    ['Create the login client', 'Credentials → Create credentials → OAuth client ID → Desktop app.'],
    ['Paste it into Hermes', 'Settings → Plugins → Google Calendars: Client ID + Client secret.']
  ]
  return jsxs('div', {
    style: { padding: 14, display: 'flex', flexDirection: 'column', gap: 12, fontSize: 12, overflowY: 'auto' },
    children: [
      jsx('div', { style: { fontSize: 14, fontWeight: 600 }, children: 'Connect your Google Calendars' }),
      jsx('div', { style: S.muted, children: 'One-time setup (about 5 minutes). You use your own Google login client, so your calendars never pass through anyone else\u2019s app.' }),
      jsx('ol', {
        style: { display: 'flex', flexDirection: 'column', gap: 8, paddingLeft: 18, listStyle: 'decimal' },
        children: steps.map(([t, d]) => jsxs('li', { children: [jsx('div', { style: { fontWeight: 600 }, children: t }), jsx('div', { style: S.muted, children: d })] }, t))
      }),
      jsxs('div', { style: S.faint, children: ['Sign-in sends the browser to ', jsx('code', { children: redirect }), '. That page won\u2019t load, which is expected: you copy its address back here.'] }),
      jsxs('div', {
        style: { display: 'flex', gap: 8 },
        children: [
          jsx(Button, { size: 'sm', onClick: () => host.navigate(`/settings?tab=plugins&plugin=${ID}`), children: 'Open plugin settings' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => openUrl('https://console.cloud.google.com/apis/credentials'), children: 'Google Cloud console' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: onRecheck, children: 'Check again' })
        ]
      })
    ]
  })
}

function AddAccount({ onDone, onCancel }) {
  const [started, setStarted] = useState(null)
  const [pasted, setPasted] = useState('')
  const [busy, setBusy] = useState(false)

  const start = async () => {
    setBusy(true)
    try {
      const s = await rest.post('/auth/start', {})
      setStarted(s)
      await openUrl(s.auth_url)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  const finish = async () => {
    setBusy(true)
    try {
      const acct = await rest.post('/auth/finish', { pasted, state: started?.state })
      host.notify({ kind: 'success', message: `Connected ${acct.email}` })
      onDone()
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  return jsxs('div', {
    style: { ...S.card, padding: 10, display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12 },
    children: [
      jsx('div', { style: { fontWeight: 600 }, children: 'Add a Google account' }),
      jsxs('div', {
        style: S.muted,
        children: [
          '1. Sign in with the account you want to add and allow calendar access. ',
          'If Google warns the app is unverified: Advanced → Go to (your app).'
        ]
      }),
      jsxs('div', {
        style: { display: 'flex', gap: 6, flexWrap: 'wrap' },
        children: [
          jsx(Button, { size: 'sm', disabled: busy, onClick: start, children: started ? 'Open sign-in again' : 'Open Google sign-in' }),
          started && jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => api.os?.writeClipboard?.(started.auth_url).then(ok => ok && host.notify({ kind: 'info', message: 'Sign-in link copied' })), children: 'Copy link' })
        ]
      }),
      started && jsx('div', { style: S.muted, children: `2. The browser ends on a page that won\u2019t load (${started.redirect_uri}...). Copy that whole address and paste it here:` }),
      started && jsx(Textarea, {
        rows: 3,
        value: pasted,
        placeholder: 'http://127.0.0.1:8765/?state=...&code=...',
        onChange: e => setPasted(e.target.value),
        className: 'font-mono text-xs',
        style: { resize: 'vertical', wordBreak: 'break-all' }
      }),
      jsxs('div', {
        style: { display: 'flex', gap: 6, justifyContent: 'flex-end' },
        children: [
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: onCancel, children: 'Cancel' }),
          started && jsx(Button, { size: 'sm', disabled: busy || !pasted.trim(), onClick: finish, children: busy ? 'Connecting…' : 'Connect' })
        ]
      })
    ]
  })
}

function AccountsScreen({ accounts, onChanged, onBack }) {
  const [adding, setAdding] = useState(accounts.length === 0)
  const [editing, setEditing] = useState(null)

  const rename = async (a, label) => {
    setEditing(null)
    if (label == null || label === a.label) return
    try {
      await rest.patch(`/accounts/${enc(a.id)}`, { label })
      onChanged()
    } catch (e) {
      fail(e)
    }
  }
  const recolor = async (a, color) => {
    try {
      await rest.patch(`/accounts/${enc(a.id)}`, { color })
      onChanged()
    } catch (e) {
      fail(e)
    }
  }
  const remove = async a => {
    if (!window.confirm(`Disconnect ${a.label}? Its calendars disappear from the panel and Google access is revoked.`)) return
    try {
      await rest.del(`/accounts/${enc(a.id)}`)
      onChanged()
    } catch (e) {
      fail(e)
    }
  }

  return jsxs('div', {
    style: { padding: 12, display: 'flex', flexDirection: 'column', gap: 10, overflowY: 'auto', height: '100%' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 6 },
        children: [
          accounts.length > 0 && jsx(IconButton, { icon: 'arrow-left', label: 'Back to calendar', onClick: onBack }),
          jsx('div', { style: { fontSize: 13, fontWeight: 600, flex: 1 }, children: 'Google accounts' }),
          !adding && jsx(Button, { size: 'sm', onClick: () => setAdding(true), children: 'Add account' })
        ]
      }),
      adding && jsx(AddAccount, { onDone: () => { setAdding(false); onChanged() }, onCancel: () => setAdding(false) }),
      accounts.length === 0 && !adding && jsx('div', { style: S.muted, children: 'No accounts connected yet.' }),
      ...accounts.map(a =>
        jsxs('div', {
          style: { ...S.card, padding: '8px 10px', display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 },
          children: [
            jsx('input', {
              type: 'color',
              value: a.color || '#4285f4',
              title: 'Account color (used where a calendar has none)',
              onChange: e => recolor(a, e.target.value),
              style: { width: 18, height: 18, border: 'none', background: 'none', padding: 0, cursor: 'pointer' }
            }),
            jsx('div', {
              style: { flex: 1, minWidth: 0 },
              children: editing === a.id
                ? jsx(Input, {
                    autoFocus: true,
                    defaultValue: a.label,
                    className: 'h-6 text-xs',
                    onBlur: e => rename(a, e.target.value.trim()),
                    onKeyDown: e => {
                      if (e.key === 'Enter') rename(a, e.currentTarget.value.trim())
                      if (e.key === 'Escape') setEditing(null)
                    }
                  })
                : jsxs('div', {
                    children: [
                      jsx('div', { style: { fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: a.label }),
                      a.label !== a.email && jsx('div', { style: { ...S.faint, fontSize: 11 }, children: a.email })
                    ]
                  })
            }),
            jsx(IconButton, { icon: 'edit', label: 'Rename', onClick: () => setEditing(a.id) }),
            jsx(IconButton, { icon: 'trash', label: 'Disconnect', onClick: () => remove(a) })
          ]
        }, a.id)
      )
    ]
  })
}

// ------------------------------------------------------------------ calendar list (show/hide)

function CalendarFilter({ accounts, onToggle, merge, onMerge }) {
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 6, padding: '6px 10px 10px', borderBottom: S.border, maxHeight: 220, overflowY: 'auto' },
    children: [
      jsxs('label', {
        style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, cursor: 'pointer', paddingBottom: 4, borderBottom: S.border },
        children: [jsx('input', { type: 'checkbox', checked: merge, onChange: () => onMerge(!merge) }), 'Merge duplicate events (same title and time)']
      }),
      ...accounts.map(a =>
      jsxs('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 2 },
        children: [
          jsxs('div', {
            style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600 },
            children: [jsx(Dot, { color: a.color }), jsx('span', { children: a.label })]
          }),
          a.error && jsx('div', { style: { color: 'var(--ui-red)', fontSize: 11 }, children: a.error }),
          ...a.calendars.map(c =>
            jsxs('label', {
              style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, paddingLeft: 12, cursor: 'pointer' },
              children: [
                jsx('input', { type: 'checkbox', checked: c.visible, onChange: () => onToggle(a, c), style: { accentColor: c.color || a.color } }),
                jsx('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: c.visible ? 'var(--ui-text-secondary)' : 'var(--ui-text-quaternary)' }, children: c.name }),
                !c.writable && jsx('span', { style: { ...S.faint, fontSize: 10 }, children: 'read-only' })
              ]
            }, c.id)
          )
        ]
      }, a.id)
    )
    ]
  })
}

// ------------------------------------------------------------------ views

function EventChip({ e, onOpen, compact }) {
  const { start } = eventSpan(e)
  return jsxs('div', {
    onClick: () => onOpen(e),
    title: [e, ...(e.copies || [])].map(x => `${e.summary} · ${calLabel(x)}`).join('\n'),
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: compact ? '1px 6px' : '5px 8px',
      borderRadius: 6,
      cursor: 'pointer',
      fontSize: 12,
      borderLeft: `3px solid ${e.color}`,
      background: 'var(--ui-bg-secondary)'
    },
    children: [
      !compact && jsx('span', { style: { ...S.muted, width: 58, flexShrink: 0, fontSize: 11 }, children: e.all_day ? 'All day' : fmtTime(start) }),
      jsx('span', { style: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: e.summary }),
      !compact && jsx('span', { style: { ...S.faint, fontSize: 10, maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: e.account_label }),
      e.copies?.length > 0 && jsx('span', { 'data-copies': e.copies.length + 1, style: { ...S.faint, fontSize: 10, flexShrink: 0 }, children: `×${e.copies.length + 1}` })
    ]
  })
}

function AgendaView({ days, events, onOpen, onNew }) {
  const today = new Date()
  const byDay = days.map(d => ({
    d,
    items: events.filter(e => {
      const { start, end } = eventSpan(e)
      return startOfDay(start) <= d && d <= startOfDay(end)
    })
  }))
  return jsx('div', {
    style: { overflowY: 'auto', height: '100%', padding: '6px 10px 16px', display: 'flex', flexDirection: 'column', gap: 10 },
    children: byDay.map(({ d, items }) =>
      jsxs('div', {
        children: [
          jsxs('div', {
            onDoubleClick: () => onNew(d),
            style: { fontSize: 11, fontWeight: 600, padding: '2px 0 4px', color: sameDay(d, today) ? 'var(--ui-accent)' : 'var(--ui-text-secondary)' },
            children: [fmtDay(d), sameDay(d, today) ? ' · Today' : '']
          }),
          items.length === 0
            ? jsx('div', { style: { ...S.faint, fontSize: 11, paddingLeft: 2 }, children: 'Nothing scheduled' })
            : jsx('div', { style: { display: 'flex', flexDirection: 'column', gap: 3 }, children: items.map(e => jsx(EventChip, { e, onOpen }, `${e.account_id}|${e.calendar_id}|${e.id}`)) })
        ]
      }, ymd(d))
    )
  })
}

/** Side-by-side columns for overlapping timed events in one day. */
function layoutDay(items) {
  const sorted = [...items].sort((a, b) => a.s - b.s || b.e - a.e)
  const groups = []
  let group = []
  let groupEnd = -1
  for (const it of sorted) {
    if (it.s >= groupEnd && group.length) {
      groups.push(group)
      group = []
    }
    group.push(it)
    groupEnd = Math.max(groupEnd, it.e)
  }
  if (group.length) groups.push(group)
  for (const g of groups) {
    const cols = []
    for (const it of g) {
      let c = cols.findIndex(end => end <= it.s)
      if (c === -1) {
        c = cols.length
        cols.push(0)
      }
      cols[c] = it.e
      it.col = c
    }
    for (const it of g) it.cols = cols.length
  }
  return sorted
}

function TimeGrid({ days, events, onOpen, onNew }) {
  const scroller = useRef(null)
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = HOUR_PX * 7
    const t = setInterval(() => setNow(new Date()), 60000)
    return () => clearInterval(t)
  }, [])

  const allDay = days.map(d => events.filter(e => {
    if (!e.all_day) return false
    const { start, end } = eventSpan(e)
    return startOfDay(start) <= d && d <= startOfDay(end)
  }))
  const timed = days.map(d =>
    layoutDay(events.filter(e => !e.all_day).flatMap(e => {
      const { start, end } = eventSpan(e)
      const dayStart = d.getTime()
      const dayEnd = addDays(d, 1).getTime()
      if (end.getTime() <= dayStart || start.getTime() >= dayEnd) return []
      const s = Math.max(start.getTime(), dayStart)
      const en = Math.min(Math.max(end.getTime(), s + 15 * 60000), dayEnd)
      return [{ ev: e, s: (s - dayStart) / 3600000, e: (en - dayStart) / 3600000 }]
    }))
  )
  const gutter = 38
  const colStyle = { flex: 1, minWidth: 0, borderLeft: S.border }

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 },
    children: [
      days.length > 1 && jsxs('div', {
        style: { display: 'flex', borderBottom: S.border, fontSize: 11 },
        children: [
          jsx('div', { style: { width: gutter, flexShrink: 0 } }),
          ...days.map(d =>
            jsx('div', {
              style: { ...colStyle, textAlign: 'center', padding: '3px 0', color: sameDay(d, now) ? 'var(--ui-accent)' : 'var(--ui-text-tertiary)', fontWeight: sameDay(d, now) ? 600 : 400 },
              children: d.toLocaleDateString([], { weekday: 'narrow', day: 'numeric' })
            }, ymd(d))
          )
        ]
      }),
      allDay.some(x => x.length) && jsxs('div', {
        style: { display: 'flex', borderBottom: S.border, maxHeight: 84, overflowY: 'auto' },
        children: [
          jsx('div', { style: { width: gutter, flexShrink: 0, fontSize: 9, ...S.faint, padding: '3px 2px' }, children: 'all day' }),
          ...allDay.map((items, i) =>
            jsx('div', {
              style: { ...colStyle, display: 'flex', flexDirection: 'column', gap: 2, padding: 2 },
              children: items.map(e => jsx(EventChip, { e, onOpen, compact: true }, `${e.account_id}|${e.calendar_id}|${e.id}`))
            }, i)
          )
        ]
      }),
      jsx('div', {
        ref: scroller,
        style: { flex: 1, overflowY: 'auto', minHeight: 0 },
        children: jsxs('div', {
          style: { display: 'flex', position: 'relative', height: HOUR_PX * 24 },
          children: [
            jsx('div', {
              style: { width: gutter, flexShrink: 0, position: 'relative' },
              children: Array.from({ length: 23 }, (_, h) =>
                jsx('div', {
                  style: { position: 'absolute', top: (h + 1) * HOUR_PX - 6, right: 4, fontSize: 9, ...S.faint },
                  children: new Date(2000, 0, 1, h + 1).toLocaleTimeString([], { hour: 'numeric' })
                }, h)
              )
            }),
            ...days.map((d, i) =>
              jsxs('div', {
                style: { ...colStyle, position: 'relative' },
                onDoubleClick: ev => {
                  const rect = ev.currentTarget.getBoundingClientRect()
                  const hour = Math.max(0, Math.min(23, Math.floor((ev.clientY - rect.top) / HOUR_PX)))
                  onNew(d, hour)
                },
                children: [
                  ...Array.from({ length: 24 }, (_, h) =>
                    jsx('div', { style: { position: 'absolute', left: 0, right: 0, top: h * HOUR_PX, borderTop: h ? '1px solid var(--ui-stroke-quaternary)' : 'none' } }, h)
                  ),
                  sameDay(d, now) && jsx('div', {
                    style: { position: 'absolute', left: 0, right: 0, top: (now.getHours() + now.getMinutes() / 60) * HOUR_PX, borderTop: '2px solid var(--ui-red)', zIndex: 2 }
                  }),
                  ...timed[i].map(it =>
                    jsxs('div', {
                      onClick: ev => {
                        ev.stopPropagation()
                        onOpen(it.ev)
                      },
                      onDoubleClick: ev => ev.stopPropagation(),
                      title: `${it.ev.summary} · ${it.ev.calendar_name}`,
                      style: {
                        position: 'absolute',
                        top: it.s * HOUR_PX + 1,
                        height: Math.max((it.e - it.s) * HOUR_PX - 2, 16),
                        left: `calc(${(it.col / it.cols) * 100}% + 1px)`,
                        width: `calc(${100 / it.cols}% - 3px)`,
                        background: 'var(--ui-bg-elevated)',
                        borderLeft: `3px solid ${it.ev.color}`,
                        borderRadius: 4,
                        padding: '1px 4px',
                        fontSize: 10,
                        lineHeight: '13px',
                        overflow: 'hidden',
                        cursor: 'pointer',
                        zIndex: 1,
                        boxShadow: '0 0 0 1px var(--ui-stroke-tertiary)'
                      },
                      children: [
                        jsx('div', { style: { fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }, children: it.ev.summary }),
                        (it.e - it.s) * HOUR_PX > 28 && jsx('div', { style: S.muted, children: fmtTime(parseDate(it.ev.start)) })
                      ]
                    }, `${it.ev.account_id}|${it.ev.calendar_id}|${it.ev.id}`)
                  )
                ]
              }, ymd(d))
            )
          ]
        })
      })
    ]
  })
}

// ------------------------------------------------------------------ event detail + editor

function EventDetail({ e, onClose, onEdit, onDeleted }) {
  const { start, end } = eventSpan(e)
  const when = e.all_day
    ? sameDay(start, end) ? `${fmtLongDay(start)} · all day` : `${fmtDay(start)} – ${fmtDay(end)} · all day`
    : `${fmtLongDay(start)} · ${fmtTime(start)} – ${fmtTime(parseDate(e.end))}`
  const del = async () => {
    const what = e.recurring ? 'this occurrence of the recurring event' : `"${e.summary}"`
    if (!window.confirm(`Delete ${what}?`)) return
    try {
      await rest.del(`/events/${enc(e.id)}`, { account_id: e.account_id, calendar_id: e.calendar_id })
      onDeleted()
    } catch (err) {
      fail(err)
    }
  }
  const row = (icon, content) =>
    content && jsxs('div', { style: { display: 'flex', gap: 8, alignItems: 'flex-start' }, children: [jsx(Codicon, { name: icon, size: 14, style: { marginTop: 1, ...S.muted } }), jsx('div', { style: { flex: 1, minWidth: 0, wordBreak: 'break-word' }, children: content })] })

  return jsx(Overlay, {
    onClose,
    children: jsxs('div', {
      style: { display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12 },
      children: [
        jsxs('div', {
          style: { display: 'flex', alignItems: 'flex-start', gap: 8 },
          children: [
            jsx('span', { style: { marginTop: 4 }, children: jsx(Dot, { color: e.color, size: 10 }) }),
            jsx('div', { style: { flex: 1, fontSize: 14, fontWeight: 600 }, children: e.summary }),
            e.writable && jsx(IconButton, { icon: 'edit', label: 'Edit', onClick: () => onEdit(e) }),
            e.writable && jsx(IconButton, { icon: 'trash', label: 'Delete', onClick: del }),
            jsx(IconButton, { icon: 'close', label: 'Close', onClick: onClose })
          ]
        }),
        row('clock', jsxs('span', { children: [when, e.recurring && jsx('span', { style: S.faint, children: ' · repeats' })] })),
        row('calendar', e.copies?.length
          ? jsxs('div', {
              style: { display: 'flex', flexDirection: 'column', gap: 2 },
              children: [
                ...[e, ...e.copies].map(x => jsxs('div', {
                  style: { display: 'flex', alignItems: 'center', gap: 6 },
                  children: [jsx(Dot, { color: x.color, size: 7 }), calLabel(x),
                    x.html_link && x !== e && jsx('a', { href: '#', onClick: ev => { ev.preventDefault(); openUrl(x.html_link) }, style: { ...S.faint, fontSize: 11 }, children: 'open' })]
                }, `${x.account_id}|${x.calendar_id}|${x.id}`)),
                e.writable && jsx('div', { style: { ...S.faint, fontSize: 11 }, children: `Edit and delete change the ${e.account_label} copy only.` })
              ]
            })
          : calLabel(e)),
        row('location', e.location),
        e.meet_link && row('device-camera-video', jsx('a', { href: '#', onClick: ev => { ev.preventDefault(); openUrl(e.meet_link) }, style: { color: 'var(--ui-accent)' }, children: 'Join video call' })),
        e.attendees.length > 0 && row('organization', jsx('div', {
          style: { display: 'flex', flexDirection: 'column', gap: 2 },
          children: e.attendees.map(a => jsxs('div', { children: [a.name || a.email, jsx('span', { style: S.faint, children: ` · ${a.response || 'no reply'}` })] }, a.email))
        })),
        e.description && row('note', jsx('div', { style: { whiteSpace: 'pre-wrap', ...S.muted }, children: e.description.replace(/<[^>]+>/g, '') })),
        !e.writable && jsx('div', { style: S.faint, children: 'Read-only calendar.' }),
        e.html_link && jsx('div', { children: jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => openUrl(e.html_link), children: 'Open in Google Calendar' }) })
      ]
    })
  })
}

function Overlay({ children, onClose }) {
  useEffect(() => {
    const onKey = ev => ev.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return jsx('div', {
    onClick: onClose,
    style: { position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 20, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: 10, overflowY: 'auto' },
    children: jsx('div', {
      onClick: ev => ev.stopPropagation(),
      style: { ...S.card, background: 'var(--ui-bg-elevated)', width: '100%', maxWidth: 460, padding: 12, boxShadow: '0 8px 30px rgba(0,0,0,0.45)' },
      children
    })
  })
}

function EventEditor({ draft, accounts, onClose, onSaved }) {
  const isNew = !draft.id
  const writable = accounts.flatMap(a => a.calendars.filter(c => c.writable).map(c => ({ a, c })))
  const initial = useMemo(() => {
    if (!isNew) {
      const { start, end } = eventSpan(draft)
      const realEnd = parseDate(draft.end)
      return {
        summary: draft.summary === '(no title)' ? '' : draft.summary,
        key: `${draft.account_id}|${draft.calendar_id}`,
        allDay: draft.all_day,
        date: ymd(start),
        endDate: ymd(end),
        startTime: draft.all_day ? '09:00' : hm(start),
        endTime: draft.all_day ? '10:00' : hm(realEnd),
        location: draft.location || '',
        description: draft.description || ''
      }
    }
    const first = writable.find(x => x.c.primary) || writable[0]
    const h = draft.hour ?? Math.min(new Date().getHours() + 1, 22)
    return {
      summary: '',
      key: first ? `${first.a.id}|${first.c.id}` : '',
      allDay: false,
      date: ymd(draft.date || new Date()),
      endDate: ymd(draft.date || new Date()),
      startTime: `${pad(h)}:00`,
      endTime: `${pad(h + 1)}:00`,
      location: '',
      description: ''
    }
  }, [])
  const [f, setF] = useState(initial)
  const [busy, setBusy] = useState(false)
  const set = (k, v) => setF(prev => ({ ...prev, [k]: v }))

  const save = async () => {
    const [accountId, calendarId] = f.key.split('|')
    if (!accountId) return fail('Pick a calendar')
    const body = { account_id: accountId, summary: f.summary.trim() || '(no title)', location: f.location, description: f.description }
    if (f.allDay) {
      if (f.endDate < f.date) return fail('The end date is before the start date')
      Object.assign(body, { all_day: true, start: f.date, end: ymd(addDays(parseDate(f.endDate), 1)) })
    } else {
      const endDate = f.endTime <= f.startTime && f.endDate === f.date ? ymd(addDays(parseDate(f.date), 1)) : f.endDate
      Object.assign(body, { all_day: false, start: localIso(f.date, f.startTime), end: localIso(endDate, f.endTime), time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone })
    }
    setBusy(true)
    try {
      if (isNew) {
        await rest.post('/events', { ...body, calendar_id: calendarId })
      } else {
        const moved = calendarId !== draft.calendar_id
        await rest.patch(`/events/${enc(draft.id)}`, { ...body, account_id: draft.account_id, calendar_id: draft.calendar_id, destination_calendar_id: moved ? calendarId : null })
      }
      host.notify({ kind: 'success', message: isNew ? 'Event created' : 'Event saved' })
      onSaved()
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const calOptions = isNew ? writable : writable.filter(x => x.a.id === draft.account_id)
  return jsx(Overlay, {
    onClose,
    children: jsxs('div', {
      style: { display: 'flex', flexDirection: 'column', gap: 9, fontSize: 12 },
      children: [
        jsx('div', { style: { fontSize: 14, fontWeight: 600 }, children: isNew ? 'New event' : draft.recurring ? 'Edit this occurrence' : 'Edit event' }),
        jsx(Input, { autoFocus: true, placeholder: 'Title', value: f.summary, onChange: e => set('summary', e.target.value) }),
        jsx(Field, {
          label: isNew ? 'Calendar' : 'Calendar (same account)',
          children: jsx('select', {
            value: f.key,
            onChange: e => set('key', e.target.value),
            style: S.input,
            children: calOptions.map(({ a, c }) => jsx('option', { style: { color: 'initial' }, value: `${a.id}|${c.id}`, children: `${a.label} · ${c.name}` }, `${a.id}|${c.id}`))
          })
        }),
        jsxs('label', { style: { display: 'flex', alignItems: 'center', gap: 6 }, children: [jsx('input', { type: 'checkbox', checked: f.allDay, onChange: e => set('allDay', e.target.checked) }), 'All day'] }),
        jsxs('div', {
          style: { display: 'grid', gridTemplateColumns: f.allDay ? '1fr 1fr' : '1.4fr 1fr 1fr', gap: 6 },
          children: f.allDay
            ? [
                jsx(Field, { label: 'From', children: jsx('input', { type: 'date', value: f.date, onChange: e => { set('date', e.target.value); if (f.endDate < e.target.value) set('endDate', e.target.value) }, style: S.input }) }, 'd'),
                jsx(Field, { label: 'To', children: jsx('input', { type: 'date', value: f.endDate, onChange: e => set('endDate', e.target.value), style: S.input }) }, 'e')
              ]
            : [
                jsx(Field, { label: 'Date', children: jsx('input', { type: 'date', value: f.date, onChange: e => { set('date', e.target.value); set('endDate', e.target.value) }, style: S.input }) }, 'd'),
                jsx(Field, { label: 'Start', children: jsx('input', { type: 'time', value: f.startTime, onChange: e => set('startTime', e.target.value), style: S.input }) }, 's'),
                jsx(Field, { label: 'End', children: jsx('input', { type: 'time', value: f.endTime, onChange: e => set('endTime', e.target.value), style: S.input }) }, 'e')
              ]
        }),
        jsx(Input, { placeholder: 'Location', value: f.location, onChange: e => set('location', e.target.value) }),
        jsx(Textarea, { placeholder: 'Description', rows: 3, value: f.description, onChange: e => set('description', e.target.value), style: { resize: 'vertical' } }),
        jsxs('div', {
          style: { display: 'flex', justifyContent: 'flex-end', gap: 6 },
          children: [
            jsx(Button, { size: 'sm', variant: 'ghost', onClick: onClose, children: 'Cancel' }),
            jsx(Button, { size: 'sm', disabled: busy || !f.key, onClick: save, children: busy ? 'Saving…' : 'Save' })
          ]
        })
      ]
    })
  })
}

// ------------------------------------------------------------------ the pane

/** One-time "What's new" after an update. CalendarPane marks fresh installs (no account yet) as seen. */
function WhatsNew() {
  const [seen, setSeen] = useState(() => api.storage.get('seenVersion', null))
  if (seen === VERSION) return null
  const dismiss = () => { api.storage.set('seenVersion', VERSION); setSeen(VERSION) }
  return jsxs('div', {
    'data-whats-new': VERSION,
    style: { margin: '6px 8px', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--ui-stroke-secondary)', background: 'var(--ui-bg-elevated)', fontSize: 12, display: 'flex', gap: 8, alignItems: 'flex-start' },
    children: [
      jsx(Codicon, { name: 'sparkle', size: 14, style: { marginTop: 1, color: 'var(--ui-accent)' } }),
      jsxs('div', {
        style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 },
        children: [
          jsx('div', { style: { fontWeight: 600 }, children: `Google Calendars updated to ${VERSION}` }),
          ...WHATS_NEW.map(line => jsx('div', { style: S.muted, children: `• ${line}` }, line)),
          jsx('a', { href: '#', onClick: ev => { ev.preventDefault(); openUrl(CHANGELOG_URL) }, style: { color: 'var(--ui-accent)', fontSize: 11 }, children: 'Full changelog' })
        ]
      }),
      jsx(IconButton, { icon: 'close', label: 'Dismiss', onClick: dismiss })
    ]
  })
}

function CalendarPane() {
  const [status, setStatus] = useState(null)
  const [calendars, setCalendars] = useState([])
  const [events, setEvents] = useState([])
  const [errors, setErrors] = useState([])
  const [loading, setLoading] = useState(false)
  const [screen, setScreen] = useState('calendar')
  const [view, setView] = useState(() => api.storage.get('view', 'agenda'))
  const [anchor, setAnchor] = useState(() => new Date())
  const [showFilter, setShowFilter] = useState(false)
  const [open, setOpen] = useState(null)
  const [editing, setEditing] = useState(null)
  const [merge, setMerge] = useState(() => api.storage.get('mergeDuplicates', true))
  const changeMerge = v => { setMerge(v); api.storage.set('mergeDuplicates', v) }
  const shown = useMemo(() => (merge ? mergeDuplicates(events) : events), [events, merge])
  const weekStart = api.storage.get('weekStart', 0)
  const range = useMemo(() => rangeFor(view, anchor, weekStart), [view, anchor, weekStart])

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await rest.get('/status'))
    } catch (e) {
      setStatus({ error: errText(e) })
    }
  }, [])
  const loadCalendars = useCallback(async (refresh = false) => {
    try {
      setCalendars((await rest.get('/calendars', { refresh: refresh ? 1 : 0 })).accounts)
    } catch (e) {
      fail(e)
    }
  }, [])
  const loadEvents = useCallback(async () => {
    setLoading(true)
    try {
      const res = await rest.get('/events', { start: range.from.toISOString(), end: range.to.toISOString() })
      setEvents(res.events)
      setErrors(res.errors || [])
    } catch (e) {
      fail(e)
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => {
    loadStatus()
    const off = host.onEvent?.(`plugin.${ID}.settings.changed`, () => loadStatus())
    return () => off?.()
  }, [loadStatus])

  const ready = status?.configured && status.accounts?.length > 0
  useEffect(() => {
    // First run (nothing connected yet): there's nothing "new" to announce later.
    if (status && !status.error && !status.accounts?.length && api.storage.get('seenVersion', null) === null) {
      api.storage.set('seenVersion', VERSION)
    }
  }, [status])
  useEffect(() => {
    if (ready) loadCalendars()
  }, [ready, loadCalendars])
  useEffect(() => {
    if (!ready) return
    loadEvents()
    const t = setInterval(loadEvents, REFRESH_MS)
    const onFocus = () => loadEvents()
    window.addEventListener('focus', onFocus)
    return () => {
      clearInterval(t)
      window.removeEventListener('focus', onFocus)
    }
  }, [ready, loadEvents])

  const refreshAll = () => {
    loadStatus()
    loadCalendars(true).then(loadEvents)
  }
  const toggleCalendar = async (a, c) => {
    const hidden = new Set(a.hidden_calendars || [])
    c.visible ? hidden.add(c.id) : hidden.delete(c.id)
    try {
      await rest.patch(`/accounts/${enc(a.id)}`, { hidden_calendars: [...hidden] })
      await loadCalendars()
      loadEvents()
    } catch (e) {
      fail(e)
    }
  }
  const step = dir => setAnchor(d => addDays(d, dir * (view === 'day' ? 1 : view === 'week' ? 7 : 14)))
  const changeView = v => {
    setView(v)
    api.storage.set('view', v)
  }

  if (!status) return jsx('div', { style: { padding: 14, ...S.muted, fontSize: 12 }, children: 'Loading…' })
  if (status.error) return jsx('div', { style: { padding: 14, fontSize: 12, color: 'var(--ui-red)' }, children: `Backend unavailable: ${status.error}. Is google-calendars in plugins.enabled (config.yaml) and the gateway restarted?` })
  if (!status.configured) return jsx(SetupScreen, { redirect: status.redirect_uri, onRecheck: loadStatus })
  if (screen === 'accounts' || status.accounts.length === 0) {
    return jsx(AccountsScreen, {
      accounts: status.accounts,
      onBack: () => setScreen('calendar'),
      onChanged: async () => {
        await loadStatus()
        await loadCalendars(true)
        loadEvents()
      }
    })
  }

  const onNew = (date, hour) => setEditing({ date, hour })
  return jsxs('div', {
    style: { position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 2, padding: '6px 8px', borderBottom: S.border, flexWrap: 'wrap' },
        children: [
          jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => setAnchor(new Date()), children: 'Today' }),
          jsx(IconButton, { icon: 'chevron-left', label: 'Previous', onClick: () => step(-1) }),
          jsx(IconButton, { icon: 'chevron-right', label: 'Next', onClick: () => step(1) }),
          jsx('div', { style: { flex: 1, fontSize: 12, fontWeight: 600, minWidth: 90, paddingLeft: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }, children: rangeLabel(view, range.days) }),
          jsx(IconButton, { icon: 'add', label: 'New event', onClick: () => onNew(view === 'agenda' ? new Date() : range.days[0]) }),
          jsx(IconButton, { icon: loading ? 'loading' : 'refresh', label: 'Refresh', onClick: refreshAll }),
          jsx(IconButton, { icon: 'list-filter', label: 'Show / hide calendars', active: showFilter, onClick: () => setShowFilter(s => !s) }),
          jsx(IconButton, { icon: 'account', label: 'Google accounts', onClick: () => setScreen('accounts') })
        ]
      }),
      jsx('div', {
        style: { display: 'flex', justifyContent: 'center', padding: '5px 8px' },
        children: jsx(Seg, { value: view, onChange: changeView, options: [['day', 'Day'], ['week', 'Week'], ['agenda', 'Agenda']] })
      }),
      jsx(WhatsNew, {}),
      showFilter && jsx(CalendarFilter, { accounts: calendars, onToggle: toggleCalendar, merge, onMerge: changeMerge }),
      errors.length > 0 && jsx('div', {
        style: { padding: '4px 10px', fontSize: 11, color: 'var(--ui-red)', borderBottom: S.border },
        children: errors.map((e, i) => jsx('div', { children: `${e.account}${e.calendar ? ` · ${e.calendar}` : ''}: ${e.error}` }, i))
      }),
      jsx('div', {
        style: { flex: 1, minHeight: 0 },
        children: view === 'agenda'
          ? jsx(AgendaView, { days: range.days, events: shown, onOpen: setOpen, onNew })
          : jsx(TimeGrid, { days: range.days, events: shown, onOpen: setOpen, onNew })
      }),
      open && jsx(EventDetail, { e: open, onClose: () => setOpen(null), onEdit: e => { setOpen(null); setEditing(e) }, onDeleted: () => { setOpen(null); loadEvents() } }),
      editing && jsx(EventEditor, { draft: editing, accounts: calendars, onClose: () => setEditing(null), onSaved: () => { setEditing(null); loadEvents() } })
    ]
  })
}

// ---- reminders (run from register(), so they fire even when the pane is closed)

const REMINDER_POLL_MS = 60 * 1000

function showReminder(r) {
  const title = r.minutes <= 1 ? `Starting now: ${r.summary}` : `In ${r.minutes} min: ${r.summary}`
  const body = [r.time, r.account_label, r.location].filter(Boolean).join(' · ')
  const link = r.meet_link || r.html_link
  // OS notification shows when Hermes is in the background; the toast covers the focused case.
  api.os?.notify?.({
    title,
    body,
    onActivate: () => link && api.os?.openExternal?.(link),
    actions: r.meet_link ? [{ id: 'join', label: 'Join', onAction: () => api.os?.openExternal?.(r.meet_link) }] : undefined
  })
  host.notify({
    kind: 'info',
    title: '📅 ' + title,
    message: body,
    action: r.meet_link ? { label: 'Join', onClick: () => api.os?.openExternal?.(r.meet_link) } : undefined
  })
}

async function pollReminders() {
  try {
    const res = await rest.get('/reminders/due')
    for (const r of res.reminders || []) showReminder(r)
  } catch {
    // gateway offline or not set up yet; try again next minute
  }
}

async function syncJobs() {
  try {
    await rest.post('/sync', {})
  } catch (e) {
    host.notify({ kind: 'warning', message: `Google Calendars: ${errText(e)}` })
  }
}

export default {
  id: ID, // must match the folder name
  name: 'Google Calendars',
  register(ctx) {
    api = ctx
    ctx.setTimeout(() => { syncJobs(); pollReminders() }, 5000)
    ctx.setInterval(pollReminders, REMINDER_POLL_MS)
    ctx.onEvent(`plugin.${ID}.settings.changed`, () => syncJobs())
    ctx.registerMany([
      {
        id: 'pane',
        area: 'panes',
        title: 'Calendar',
        data: { placement: 'right', width: '380px' },
        render: () => jsx(CalendarPane, {})
      },
      { id: 'page', area: ROUTES_AREA, title: 'Calendar', data: { path: PAGE }, render: () => jsx(CalendarPane, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PAGE, label: 'Calendar', codicon: 'calendar' } },
      {
        id: 'open-page',
        area: PALETTE_AREA,
        data: { id: `${ID}.open`, label: 'Open Calendar', keywords: ['calendar', 'google', 'events', 'schedule'], run: () => host.navigate(PAGE) }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: `${ID}.settings`,
          label: 'Google Calendars: plugin settings',
          keywords: ['calendar', 'google', 'oauth'],
          run: () => host.navigate(`/settings?tab=plugins&plugin=${ID}`)
        }
      }
    ])
  }
}
