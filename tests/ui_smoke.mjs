// UI smoke test: renders desktop/plugin.js in jsdom with React (the app's own
// version), a stub @hermes/plugin-sdk and a fake backend, then drives the panel.
//   node tests/ui_smoke.mjs <path-to-node_modules-with-react-react-dom-jsdom>
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

const here = dirname(fileURLToPath(import.meta.url))
const nm = resolve(process.argv[2])
const req = createRequire(join(nm, '..', 'x.js'))
const { JSDOM } = req('jsdom')
const dom = new JSDOM('<!doctype html><div id="root" style="height:800px"></div>', { pretendToBeVisual: true })
Object.assign(globalThis, { window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, getComputedStyle: dom.window.getComputedStyle,
  IS_REACT_ACT_ENVIRONMENT: true })
const React = req('react')
const { createRoot } = req('react-dom/client')
const { act } = React

// ---- stub SDK + rewritten plugin
const notes = []
globalThis.__sdk = {
  Button: ({ children, onClick, disabled, title }) => React.createElement('button', { onClick, disabled, title }, children),
  Input: props => React.createElement('input', props),
  Textarea: props => React.createElement('textarea', props),
  Codicon: ({ name }) => React.createElement('i', { 'data-icon': name }),
  PALETTE_AREA: 'palette',
  ROUTES_AREA: 'routes',
  SIDEBAR_NAV_AREA: 'sidebar.nav',
  host: { navigate: p => notes.push(['nav', p]), notify: n => notes.push(['notify', n.kind, n.message, n]), onEvent: () => () => {} }
}
const tmp = mkdtempSync(join(tmpdir(), 'gcal-ui-'))
const src = readFileSync(join(here, '..', 'desktop', 'plugin.js'), 'utf8')
  .replace(/import \{([^}]+)\} from '@hermes\/plugin-sdk'/, 'const {$1} = globalThis.__sdk')
  .replace(/from 'react\/jsx-runtime'/, `from '${pathToFileURL(req.resolve('react/jsx-runtime')).href}'`)
  .replace(/from 'react'/, `from '${pathToFileURL(req.resolve('react')).href}'`)
writeFileSync(join(tmp, 'plugin.mjs'), src)
const plugin = (await import(pathToFileURL(join(tmp, 'plugin.mjs')).href)).default

// ---- fake backend
const state = { configured: false, accounts: [], hidden: [] }
const calls = []
const ev = (id, summary, start, end, extra = {}) => ({ id, summary, start, end, all_day: start.length === 10, account_id: 'me@work.com',
  account_label: 'Work', calendar_id: 'me@work.com', calendar_name: 'Work', color: '#4285f4', writable: true, recurring: false,
  attendees: [], description: '', location: '', ...extra })
const today = new Date()
const iso = (h, m = 0) => new Date(today.getFullYear(), today.getMonth(), today.getDate(), h, m).toISOString()
const ymd = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1)
const events = [ev('a', 'Standup', iso(9), iso(9, 15), { recurring: true }), ev('b', 'Overlap', iso(9), iso(10)),
  ev('c', 'Holiday', ymd(today), ymd(tomorrow), { writable: false, calendar_name: 'Holidays' }),
  ev('d1', 'Off', ymd(today), ymd(tomorrow), { calendar_name: 'Work' }),
  ev('d2', 'Off', ymd(tomorrow), ymd(new Date(today.getFullYear(), today.getMonth(), today.getDate() + 2)), { account_id: 'me@other.com', account_label: 'Other' }),
  ev('c2', 'Holiday', ymd(today), ymd(tomorrow), { writable: false, calendar_name: 'Holidays', account_id: 'me@other.com', account_label: 'Other', html_link: 'https://cal/other' })]
const storage = new Map()
const osNotes = []
const due = []
const ctx = {
  storage: { get: (k, d) => (storage.has(k) ? storage.get(k) : d), set: (k, v) => storage.set(k, v) },
  os: { openExternal: async url => (calls.push(['open', url]), true), writeClipboard: async () => true,
    notify: n => (osNotes.push(n), true) },
  registerMany: cs => (ctx.contribs = cs),
  timers: [],
  setTimeout: (fn, ms) => (ctx.timers.push(['timeout', fn, ms]), () => {}),
  setInterval: (fn, ms) => (ctx.timers.push(['interval', fn, ms]), () => {}),
  onEvent: (type, fn) => (ctx.listeners.push([type, fn]), () => {}),
  listeners: [],
  rest: async (path, opts = {}) => {
    const method = opts.method || 'GET'
    calls.push([method, path, opts.body])
    const p = path.split('?')[0]
    if (p === '/reminders/due') return { reminders: due.splice(0) }
    if (p === '/sync') return { jobs: {} }
    if (p === '/status') return { configured: state.configured, accounts: state.accounts, redirect_uri: 'http://127.0.0.1:8765/' }
    if (p === '/auth/start') return { auth_url: 'https://accounts.google.com/o/oauth2/v2/auth?x', state: 'st', redirect_uri: 'http://127.0.0.1:8765/' }
    if (p === '/auth/finish') {
      state.accounts = [{ id: 'me@work.com', email: 'me@work.com', label: 'Work', color: '#4285f4', hidden_calendars: [] }]
      return state.accounts[0]
    }
    if (p === '/calendars') return { accounts: state.accounts.map(a => ({ ...a, calendars: [
      { id: 'me@work.com', name: 'Work', color: '#4285f4', primary: true, writable: true, visible: true },
      { id: 'holidays', name: 'Holidays', color: '#0b8043', primary: false, writable: false, visible: !state.hidden.includes('holidays') }] })) }
    if (p === '/events' && method === 'GET') return { events, errors: [] }
    if (p === '/events' && method === 'POST') return ev('new', opts.body.summary, opts.body.start, opts.body.end)
    if (p.startsWith('/events/') && method === 'DELETE') return { ok: true }
    if (p.startsWith('/accounts/') && method === 'PATCH') { state.hidden = opts.body.hidden_calendars || state.hidden; return {} }
    throw new Error(`unexpected ${method} ${path}`)
  }
}
plugin.register(ctx)
const pane = ctx.contribs.find(c => c.area === 'panes')

// ---- helpers
let failures = 0
const check = (cond, msg) => { console.log(`${cond ? 'PASS' : 'FAIL'} ${msg}`); if (!cond) failures++ }
const text = () => document.body.textContent
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise(r => setTimeout(r, 5)) }) }
const byText = (sel, t) => [...document.querySelectorAll(sel)].find(el => el.textContent.trim() === t)
const byTitle = t => document.querySelector(`[title="${t}"]`)
const click = async el => { await act(async () => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))); await flush() }
const type = async (el, v) => {
  const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v)
  await act(async () => el.dispatchEvent(new window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })))
}
window.confirm = () => true
const errors = []
const origError = console.error
console.error = (...a) => { errors.push(a.join(' ')); }

// ---- run
check(pane && pane.data.placement === 'right', 'registers a right-side pane')
const page = ctx.contribs.find(c => c.area === 'routes'), nav = ctx.contribs.find(c => c.area === 'sidebar.nav')
check(page && nav && page.data.path === '/calendar' && nav.data.path === '/calendar' && nav.data.label === 'Calendar', 'registers a full-page route + sidebar link')

// reminders: polled from register() (not the pane), shown as OS notification + toast
const poll = ctx.timers.find(t => t[0] === 'interval')
check(poll && poll[2] === 60000, 'reminders poll every minute from register()')
check(ctx.listeners.some(l => l[0] === 'plugin.google-calendars.settings.changed'), 'settings changes re-sync scheduled jobs')
await act(async () => { ctx.timers.find(t => t[0] === 'timeout')[1]() }); await flush()
check(calls.some(c => c[0] === 'POST' && c[1] === '/sync'), 'startup syncs scheduled jobs')
due.push({ id: 'r1', summary: 'Standup', minutes: 10, time: '9:00 AM', account_label: 'Work', location: '', meet_link: 'https://meet.google.com/abc', html_link: 'https://cal/x' })
await act(async () => { await poll[1]() }); await flush()
check(osNotes.length === 1 && osNotes[0].title === 'In 10 min: Standup' && osNotes[0].body.includes('9:00 AM'), 'due reminder -> OS notification')
const toast = notes.find(n => n[0] === 'notify' && n[3]?.title?.includes('Standup'))
check(toast && toast[3].action?.label === 'Join', 'due reminder -> in-app toast with Join')
toast?.[3].action.onClick()
check(calls.some(c => c[0] === 'open' && c[1] === 'https://meet.google.com/abc'), 'Join opens the meeting link')
await act(async () => { await poll[1]() }); await flush()
check(osNotes.length === 1, 'nothing due -> no notification')
calls.length = 0
const root = createRoot(document.getElementById('root'))
await act(async () => root.render(pane.render()))
await flush()
check(text().includes('Connect your Google Calendars'), 'unconfigured -> setup screen')
await click(byText('button', 'Open plugin settings'))
check(notes.some(n => n[0] === 'nav' && n[1].includes('plugin=google-calendars')), 'setup button opens the plugin settings page')

state.configured = true
await click(byText('button', 'Check again'))
check(text().includes('Add a Google account'), 'configured, no accounts -> add account flow')
await click(byText('button', 'Open Google sign-in'))
check(calls.some(c => c[0] === 'open' && c[1].startsWith('https://accounts.google.com')), 'opens Google sign-in in the browser')
await type(document.querySelector('textarea'), 'http://127.0.0.1:8765/?state=st&code=abc')
await click(byText('button', 'Connect'))
const fin = calls.find(c => c[1] === '/auth/finish')
check(fin && fin[2].pasted.includes('code=abc') && fin[2].state === 'st', 'sends the pasted URL + state')
check(!text().includes('Add a Google account') && byTitle('Google accounts'), 'first account connected -> straight to the calendar')
check(text().includes('Standup') && text().includes('Holiday'), 'agenda shows merged events')
await click(byTitle('Google accounts'))
check(text().includes('me@work.com') && text().includes('Work') && byTitle('Back to calendar'), 'accounts screen lists the account with a way back')
await click(byTitle('Back to calendar'))
check(text().includes('Today'), 'agenda marks today')

await click([...document.querySelectorAll('div')].find(d => d.getAttribute('title') === 'Standup · Work'))
check(text().includes('repeats') && byTitle('Edit') && byTitle('Delete'), 'detail shows recurring + edit/delete')
await click(byTitle('Delete'))
check(calls.some(c => c[0] === 'DELETE' && c[1].startsWith('/events/a?') && c[1].includes('calendar_id=me%40work.com')), 'delete calls the API with account+calendar')

const holChips = () => [...document.querySelectorAll('div')].filter(d => (d.getAttribute('title') || '').startsWith('Holiday · '))
check([...document.querySelectorAll('div')].filter(d => (d.getAttribute('title') || '').startsWith('Off · ')).length === 2, 'same title on different days stays separate')
check(holChips().length === 1 && holChips()[0].querySelector('[data-copies="2"]'), 'same title + time on two accounts -> one chip marked x2')
await click(holChips()[0])
check(!byTitle('Edit') && text().includes('Read-only calendar'), 'read-only event has no edit/delete')
check(text().includes('Holidays · Work') && text().includes('Holidays · Other'), 'merged event lists every calendar it is on')
await click(byTitle('Close'))

await click(byText('button', 'Week'))
check(storage.get('view') === 'week', 'view choice persisted')
const blocks = [...document.querySelectorAll('div')].filter(d => ['Standup · Work', 'Overlap · Work'].includes(d.getAttribute('title')))
check(blocks.length === 2 && blocks[0].style.width !== '' && blocks[0].style.width === blocks[1].style.width && blocks[0].style.left !== blocks[1].style.left,
  'overlapping events sit side by side in the week grid')
await click(byText('button', 'Day'))
check(text().includes(new Date().toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })), 'day view shows today')

await click(byTitle('New event'))
check(text().includes('New event'), 'new event editor opens')
const sel = document.querySelector('select')
check(sel && [...sel.options].every(o => !o.value.includes('holidays')), 'read-only calendars are not offered for new events')
await type(document.querySelector('input[placeholder="Title"]'), 'Lunch')
await type(document.querySelector('input[type="time"]'), '12:00')
await type(document.querySelectorAll('input[type="time"]')[1], '13:00')
await click(byText('button', 'Save'))
const post = calls.find(c => c[0] === 'POST' && c[1] === '/events')
check(post && post[2].summary === 'Lunch' && /T12:00:00[+-]\d\d:\d\d$/.test(post[2].start) && /T13:00:00[+-]\d\d:\d\d$/.test(post[2].end) && post[2].calendar_id === 'me@work.com',
  `create sends local times with offset (${post && post[2].start})`)

await click(byTitle('New event'))
await click(document.querySelector('input[type="checkbox"]'))
await click(byText('button', 'Save'))
const allDay = calls.filter(c => c[0] === 'POST' && c[1] === '/events').pop()
check(allDay[2].all_day === true && allDay[2].end > allDay[2].start && allDay[2].start.length === 10, 'all-day event sends an exclusive end date')

await click(byTitle('Show / hide calendars'))
const mergeBox = [...document.querySelectorAll('label')].find(l => l.textContent.includes('Merge duplicate')).querySelector('input')
await click(mergeBox)
check(holChips().length === 2 && storage.get('mergeDuplicates') === false, 'merge toggle off -> copies shown separately (remembered)')
await click(mergeBox)
check(holChips().length === 1, 'merge toggle back on')
const hol = [...document.querySelectorAll('label')].find(l => l.textContent.includes('Holidays'))
await click(hol.querySelector('input'))
check(calls.some(c => c[0] === 'PATCH' && c[2]?.hidden_calendars?.includes('holidays')), 'unticking a calendar hides it (saved on the account)')

check(errors.filter(e => !/act\(/.test(e)).length === 0, `no React errors (${errors.slice(0, 2).join(' | ')})`)
console.error = origError
await act(async () => root.unmount())
console.log(failures ? `${failures} FAILED` : 'ALL PASS')
process.exit(failures ? 1 : 0)
