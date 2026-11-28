# web chat panel (PD-24)

**Status: design only, agreed in PD-24. Nothing is built.** PD-25 builds the panel and PD-26
builds the consent screen it links to. When code lands, this file is updated in the same change,
as `ui-architecture.md` is (`.claude/rules/architecture.md`).

The contract this design works within is `agent`'s
[Chat UX contract with `web`](../../agent/docs/architecture.md#chat-ux-contract-with-web):

- the browser posts to `web` with CSRF;
- `web` reads `agent`'s stream of typed `AgentEvent`s and turns each one into HTML;
- `agent` never emits HTML;
- a browser without JS gets the whole answer as one page;
- `web`'s pages are unaffected when `agent` is down.

This file decides what that contract left to `web`: where the panel sits, its states, how the
answer reaches the browser, and how it behaves without JS, for a screen reader and under the
brand rules. Pedro's own voice is in [`persona.md`](../../agent/docs/persona.md). The copy below
is `web`'s, and follows chapter IV of the [brand charter](../../../brand/brand-charter.html).

All figures and tickers in examples are made up.

## Decisions, in short

- **A side panel**, opened from an "Ask Pedro" button in the header. On a wide screen it sits
  beside the page. Narrower, it covers the page's right side. On a phone it fills the screen.
- **One ongoing conversation**, with "New chat" to start another. `agent` keeps the old ones, but
  v1 has no list of them.
- **A small fetch-stream script** carries the answer. The htmx SSE extension was rejected (see
  "Transport").
- **`web` renders every byte the user sees**, model text included, on the server. The script only
  puts server-rendered fragments in place.
- **Without JS**, "Ask Pedro" is a link to a `/pedro` page with the same content. A message is a
  plain form post that returns the full conversation once the answer is complete.

## Placement and layout

### The panel in the app shell

- **Markup.** `base.html` renders an `<aside id="pedro-panel" aria-label="Pedro">` after `<main>`,
  for a logged-in user only, and `hidden` until opened. The auth pages, the error pages and the 403
  page have no user, so they have no panel.
- **The button.** "Ask Pedro" sits in the header, just before the account menu.
  - It's a link: `href="/pedro?from=<current path and query>"`.
  - `static/pedro_panel.js` turns it into a toggle, with `aria-expanded` and `aria-controls`.
  - A modified click still opens `/pedro` in a new tab, the delete dialog's convention.
  - Without JS, the link is the whole feature.
  - It's `.btn-secondary`, not gold. Gold is the one accent, and it belongs to Send inside the
    panel (charter, chapter II).
- **Not in the account menu.** The menu holds things that aren't sections of the portfolio:
  admin, theme, log out. Pedro is something to reach for while reading a page, and one more click
  inside a `<details>` hides it.

### Three widths

```text
Docked (window ≥ chat-dock, about 1400px)
┌──────────────────────────────────────────────────────────────────────┐
│ ◆  Overview Portfolio Labs Risk Accounts Tickers  [Ask Pedro] Ana ▾ │
├───────────────────────────────────────────────┬──────────────────────┤
│ page content                                  │ Pedro   New chat  ×  │
│ (narrower by the panel; its own layouts       │ ──────────────────── │
│  respond to the width they get, see below)    │            You said: │
│                                               │  What's driving my   │
│                                               │  risk?               │
│                                               │ Pedro said:          │
│                                               │  Volatility is 14.2% │
│                                               │  a year over 3 years │
│                                               │  …                   │
│                                               │ · Reading your risk  │
│                                               │   figures…           │
│                                               │ ┌──────────────────┐ │
│                                               │ │ Ask about your   │ │
│                                               │ │ portfolio        │ │
│                                               │ └──────────────────┘ │
│                                               │               [Send] │
│                                               │ Not advice: see note │
└───────────────────────────────────────────────┴──────────────────────┘

Overlay (tablet to chat-dock)
┌──────────────────────────────────────────────────┐
│ ◆  Overview Portfolio …        [Ask Pedro] Ana ▾ │
├─────────────────────────────┬────────────────────┤
│ page content, full width,   │ Pedro  New chat  × │
│ partly covered on the right │ …                  │
│ (still scrolls and is       │ [textarea]  [Send] │
│  usable on the left)        │                    │
└─────────────────────────────┴────────────────────┘

Phone (≤ 600px)
┌──────────────────────────┐
│ Pedro     New chat     × │
│ ──────────────────────── │
│ messages …               │
│                          │
│ [textarea]        [Send] │
└──────────────────────────┘
```

- **Docked**, from a new **`chat-dock`** breakpoint:
  - `body` becomes a two-column grid when the panel is open: the page, then a 400px panel.
  - The panel is `position: sticky` and as tall as the viewport, so it stays beside the page as
    the page scrolls.
  - The page keeps at least 880px, the old single column. With the panel, a 24px gap and the 3vw
    gutters, that's a window of about 1400px. PD-25 measures it, as the LY breakpoints were
    measured, and lists it by `--content-max`.
- **Overlay**, between the stack breakpoint (600px) and `chat-dock`:
  - The same 400px panel is `position: fixed` on the right, over the page, with a hairline and the
    `--backdrop` shadow edge.
  - It isn't modal. The page's left side is still visible and usable, which is the point of a
    panel over a separate page.
- **Phone**, at 600px and below: the panel fills the viewport (`inset: 0`).
  - The script sets `inert` on the header and `<main>` while it's open, so Tab can't wander into a
    page the user can't see.
  - Close (or Escape) removes `inert` and returns focus to "Ask Pedro".
  - At this width the button reads "Pedro" and shares the header's first row with the mark and
    the user's name. PD-25 measures that row at 360px with a long display name.
- **Why `inert` and not a `<dialog>`.** One element has to be modal only on a phone. A `<dialog>`
  opened with `show()` versus `showModal()` would need re-opening whenever the window crosses the
  breakpoint, and `showModal()`'s top layer would hide the page on every width.
- **CSS** goes in `layout.css`, beside `.split`. `web.css` is near the 64KB limit (UX-11).

### Sharp edge: the page's layouts must respond to the page, not the window

Every `.split` layout (LY-4 to LY-6, UX-8, UX-11) switches at a *viewport* breakpoint, measured
for the full content width. A docked panel takes about 424px from the page and doesn't change the
viewport.

- **Example.** `/portfolio` at a 1600px window is past its 1480px `.split` breakpoint, so it lays
  out two columns that need 1384px of content. Docked, it has about 1080px, and the table
  overflows its column.
- **Fix (PD-25's preparatory refactor).** Make `.page` a size container
  (`container-type: inline-size`) and move the `.split` breakpoints from `@media` to `@container`
  queries, converting each window width to the content width it was measured from. The arithmetic
  in `ui-architecture.md` is already done in content widths, so this is a unit change, not a
  re-measure.
  - Container queries are Baseline since 2023.
  - `review_pages.py` then runs each page with the panel docked as well, at the widths where it
    docks.
  - Confidence: medium-high. The risk is a `.split` rule that leaned on a viewport unit. PD-25
    checks each one.
- **Rejected:**
  - *Only dock above every `.split` breakpoint plus the panel* (about 1900px): most laptops would
    never see a docked panel.
  - *Always overlay*: it hides the figures the user is asking about, which is why the overlay
    drawer lost to the side panel.

### Loading and open state

- **Lazy.** The panel's body is fetched only when it opens (`GET /pedro/panel`, a fragment). A
  page view never calls `agent`, so a slow or down `agent` costs a page nothing (the contract's
  availability rule).
- **Open across navigation.** Every page here is a full navigation, so an open panel would close
  on each link.
  - The script stores the panel's state in a `stakehold_pedro` cookie (`open`/absent; not
    `HttpOnly`, since the script writes it, and it holds nothing sensitive).
  - The server renders `<aside>` without `hidden` when the cookie says open. The panel then
    appears in the first paint, with no flash, the reason the theme is a server-read cookie.
  - The body still loads lazily after the page, with `hx-trigger="load"` on the empty panel.
  - Without JS the cookie is never set, so the panel never renders open, and `/pedro` is the path.
  - *Assumption, for the owner to confirm in review:* the panel should stay open across
    navigation. The alternative is closed on every page, which needs no cookie.

### The `/pedro` page

- It renders the panel's body as an ordinary page under the app shell (`nav_active` unset). It's
  the no-JS path and a deep link.
- **`from`.** Its `from` parameter is the page the user came from. It sets the page context (see
  below), and a "Back to <page>" link returns there.
  - `from` is a redirect target, so it goes through `theme.safe_next`'s same-site check.
  - Anything that check refuses becomes `/` and "unknown page".
- With JS, `/pedro` still works. It's the same fragment and the same script, so it streams like
  the panel.

## Anatomy

From top to bottom, the panel's body (`GET /pedro/panel`, also the body of `/pedro`):

1. **Header.**
   - "Pedro" in Fraunces, the one display-font line in the panel.
   - "New chat", a `.btn-ghost` form post.
   - Close (`×`, with `aria-label="Close Pedro"`), shown only in the panel, not on `/pedro`.
2. **Log.** The conversation's messages, oldest first, in a `.pedro-log` that scrolls on its own.
   - Each message is an `<article>` whose first child is a visually hidden "You said:" or "Pedro
     said:".
   - The user's messages sit right-aligned, with a hairline.
   - Pedro's sit left-aligned on `--surface`, full width, because they're the longer ones and they
     carry lists.
   - No avatars and no gold bubbles.
3. **Status line.** One muted line under the log, for tool status. It's empty and takes no space
   when there's no status.
4. **Composer.**
   - A `<textarea name="message" rows="3">`, labelled "Message to Pedro" and visually hidden, with
     the placeholder "Ask about your portfolio".
   - Send, a gold `.btn-primary`. Stop, a `.btn-secondary`, takes Send's place while a run
     streams.
   - `maxlength` matches whatever cap `agent`'s message input sets. PD-25 reads it from there, and
     doesn't invent one in `web`.
5. **Footer.** One muted line: *"Pedro explains your figures. He doesn't give financial advice,
   and the decisions are yours."*
   - Persona.md's "Not advice" keeps disclaimers out of Pedro's replies, so the panel says it once,
     here, instead.
   - The wording is for the owner to confirm.

## States

Each state's trigger, what's shown, and what stays usable. The events are `agent`'s
(`architecture.md`, "Internal layering" and "Loop policy"). Where `agent` reports something only
as a `run_failed` reason, the state names the reason.

| State | Trigger | Shown | Usable |
|---|---|---|---|
| Empty | the latest conversation has no messages, or there is none | greeting, suggestions | composer, suggestions |
| Consent needed | consent is `off` when the panel loads; or `consent_required` / `no_permitted_model` on a turn | explanation, link to settings | link only |
| Streaming | `run_started` until the run ends | Pedro's message growing; Stop | Stop, the page |
| Tool status | `tool_started` / `tool_finished` | status line | as Streaming |
| Unavailable | `agent` unreachable, or 5xx, before a stream starts | notice, Retry | Retry, the page |
| Budget exceeded | `budget_exceeded` | the answer so far, then a note | composer |
| Incomplete | length stop, Stop pressed, stream dropped | the answer so far, marked | composer |
| Error | `run_failed`, `provider_unavailable` | the answer so far (if any), error banner | composer |
| Busy elsewhere | `agent` rejects a message because a run is already streaming (another tab) | warning banner | composer, once that run ends |

### Empty

- **Greeting**, one line: *"Ask about your portfolio's holdings, performance or risk. I read the
  same figures this page shows."*
- **Suggestions.** Two or three questions for the current page, as buttons. This is fixed
  per-route copy in `web`, with no model call.
  - Each is a submit button, `name="suggestion"`, in the composer's form. A click sends that
    question, with or without JS. The route uses `suggestion` when it's present, and `message`
    otherwise.
  - They name only what the current toolset answers. Slice 1 has performance and ex-post risk;
    concentration and backtests arrive with slice 2 (`architecture.md`, "The tools"). So until
    slice 2, Labs suggests a question about the portfolio, not about the mix.
  - Draft:
    - Overview and Portfolio: "How has my portfolio done?", "What are my largest holdings?"
    - Risk: "What does my volatility figure mean?", "How risky is my portfolio?"
    - Labs: "How has my portfolio done?" (until slice 2)
    - Elsewhere: Overview's.

### Consent needed

- **Before a first message.** On load, `GET /pedro/panel` asks `agent` for the user's consent.
  When it's `off`, the composer is replaced by:
  > **Pedro is off.** To answer, Pedro sends your portfolio's figures to an AI model provider. You
  > choose whether that's allowed. [Choose in settings]
  - The link goes to PD-26's settings screen.
  - The screen, not the panel, says exactly what leaves the deployment
    (`architecture.md`, "Consent and model routing").
- **On a turn.** A `consent_required` or `no_permitted_model` event renders the same block in the
  log. Consent can change between loading the panel and sending.
- **Dependency.** `agent`'s API sketch has no consent read yet. PD-25 needs one, and PD-26 has to
  save through `agent` anyway. If the read fails, the panel shows the composer, and the turn's own
  `consent_required` catches it. Consent is resolved before every run, so a missed read can't
  send anything.

### Streaming and tool status

- **Pedro's message.** It appears when `run_started` arrives, empty, with `aria-busy="true"`, and
  grows as text arrives (see "Transport").
- **The composer.** Send gives way to Stop, and the textarea stays editable. Sending a second
  message is blocked while a run streams, because `agent` rejects it (one run per conversation).
  So the user can type the next question while the answer streams, but can't send it yet.
- **Tool status.**
  - `tool_started` puts a label on the status line, from a tool-name-to-label map in `web`:

    | Tool | Label |
    |---|---|
    | `get_performance` | Reading your returns… |
    | `get_risk` | Reading your risk figures… |
    | `get_concentration` | Reading what your funds hold… |
    | `get_backtest` | Running the backtest… |
    | anything else | Looking something up… |

    `get_holdings` is preloaded every turn, so it gets no label. A label each turn would only say
    the same thing. Arguments and results are never shown (the contract keeps them server-side).
  - `tool_finished`:
    - status `ok`: clears the line;
    - `error` or `refused`: leaves *"Couldn't read your risk figures."* until the run ends. Pedro
      is told and says so in his answer (`architecture.md`, "Failure isolation"), so the line is
      only the live view of it.
  - Parallel calls (up to 4) show the label of the most recent unfinished one.

### Unavailable

- **When.** `agent` can't be reached, times out on connect, or answers 5xx before the stream
  starts, for the panel's load or for a message:
  > **Pedro is unavailable right now.** Your pages work as usual. [Try again]
- **Try again** repeats the panel load (`hx-get`), or re-sends the same message. The textarea
  keeps it, since the post didn't start a run.
- Nothing else on the page changes. This is a warning banner (the `_banner.html` macro), not an
  error: Pedro being down isn't something the user did wrong.

### Budget exceeded

- **The answer stays.** The loop's last allowed model call has no tools, so a turn that runs out
  of steps still answers (`architecture.md`, "Loop policy"). After it, a muted note:
  > Pedro reached his limit for one answer. A narrower question can go further.
- **Which budget.** For a token or cost budget, the answer may be cut short. Such an answer is also
  marked incomplete (below). The note is the same for every budget: the user can't act on which
  one tripped, and the run's record names it.
- **Later.** A per-user *daily* budget comes later (`architecture.md`, "Failure isolation and
  limits"). Its copy will say so and when it resets. It isn't designed here.

### Incomplete

- The partial text stays, followed by a muted *"This answer is incomplete."* This happens on:
  - a stop on the model's output-length limit;
  - Stop pressed;
  - a stream dropped mid-answer.
- It matches `agent`'s record, which keeps partial text marked incomplete (the
  `conversation-recording` rule), so the reloaded conversation shows the same thing.
- **After Stop**, the note comes from a `<template>` in the panel. Once the fetch is aborted the
  server can't send anything more, and the copy still lives in a template, not in JS
  (`request-failed-banner`'s convention).

### Error

- **On `run_failed` or `provider_unavailable`:** any partial text stays, marked incomplete, and an
  error banner follows in the log:
  > **Pedro couldn't finish this answer.** Send it again, or ask something else.
- **Provider unavailable** gets its own lead: *"The AI model provider isn't answering."* It's a
  different fact for the user: nothing about their question failed.
- **The colour.** Never oxblood (a loss) and never gold. This is the banner's ink rule
  (`ui-architecture.md`, UI-12).
- **The conversation stays usable.** A failed run is one run (`architecture.md`, "Run boundary").

### Action proposals

`action_proposed` is stage 2. v1 has no proposal tools, so it shouldn't arrive. If one does, it's
ignored. A proposal renders as a button only once `web` maps that kind to a route
(`architecture.md`, "Action proposals").

## Transport

**Decided: a fetch-stream script.** Confidence: high on the choice, medium on the details below
until PD-25 runs them.

### Request

- **Script.** `pedro_panel.js` intercepts the composer's submit and `fetch`es the same
  `POST /pedro/messages` with the form's body. The body carries the `csrf_token`, the message or
  suggestion, the conversation id and the page context.
- **CSRF.** The existing double-submit check (`api/csrf.py`), unchanged.
- **Stop** aborts the fetch (`AbortController`). `web` sees the client disconnect and closes its
  stream to `agent`, which records the run as aborted and keeps its partial text.

### Response

`web` answers `text/event-stream`. Each SSE event carries one **server-rendered HTML fragment**:

| Event | Fragment | Script does |
|---|---|---|
| `user` | the user's message `<article>` | appends to the log |
| `answer` | Pedro's whole message so far | replaces Pedro's message, by id |
| `status` | the status line's content | replaces the status line |
| `done` | Pedro's final message, plus any note (budget, incomplete) or banner | replaces Pedro's message, clears status, restores Send |

- **Fragments, not JSON.** The script never builds markup from model text, or from any text. Every
  fragment is escaped and rendered by `web`'s templates. That's the contract's "`web` keeps sole
  control of escaping", carried into the browser.
  - *Rejected: JSON events rendered by the script*, which would put a second renderer, and its
    escaping, in JS, with no JS test runner to check it.
- **Framing.** A fragment's newlines become one `data:` line each, as SSE requires. The parser is
  about 30 lines: split on blank lines, join the `data:` lines, dispatch on `event:`.
- **Rendering while it streams.** On each flush `web` re-renders the *whole* message's markdown
  and sends it as one `answer`.
  - Flushes are throttled to about every 150ms, and always one at the end.
  - Rendering only the new text would render half-open markdown: a `**` with no partner yet, a
    list cut mid-item.
  - Known cost: the bytes sent grow with the square of the answer's length. At the 8k output-token
    budget, about 30KB of text, that's a few hundred KB over a turn on a local connection. Fine for
    v1, and it shows up in the network panel if it ever isn't.
- **The user's message** comes back from the server as the first event rather than being echoed
  by the script, so it's escaped like everything else.

### Why not the htmx SSE extension

- **EventSource is GET-only.** It can't carry a POST body, so it can't carry the CSRF token, the
  message or the page context. A turn would take two requests: a POST that starts the run and
  returns an id, then a GET that streams it.
- **A handoff `web` doesn't have.** `web` is stateless (`architecture.md`). Handing the run from
  the POST to the GET would need shared state, a store or a signed token, plus a rule for a POST
  whose GET never comes.
- **One more vendored file**, version-pinned to htmx 1.9's extension API.
- **Its strength doesn't apply.** EventSource reconnects on its own, but a chat turn can't resume
  mid-answer anyway. A dropped stream is "incomplete".

### Sharp edges for PD-25

- **`SecurityHeadersMiddleware` is a `BaseHTTPMiddleware`.** It must pass a streaming response
  through as it streams, not buffer it. Check with a test that reads the first event before the
  fake `agent` finishes.
- **No buffering on the way out:**
  - set `Cache-Control: no-cache` and `X-Accel-Buffering: no` on the response;
  - any compression middleware added later must skip `text/event-stream`.
- **The CSP needs no change.** A same-origin `fetch` is covered by `default-src 'self'`.
- **Timeouts.** `web`'s read timeout to `agent` must outlast the 120s turn wall clock. Any proxy in
  front of `web` needs the same.

## Without JS

- **The flow.**
  - "Ask Pedro" goes to `/pedro?from=…`, and the composer is a plain
    `<form method="post" action="/pedro/messages">`.
  - The route reads `agent`'s whole stream itself, then answers `303` to `/pedro?from=…`, which
    renders the conversation with the new answer (post, redirect, get, as the other forms do).
  - Every state above renders the same way, as part of that page.
- **Known cost:** the user sees a loading tab for as long as the turn takes, up to the 120s wall
  clock. That's the contract's "one page render after the run finishes".
- **What still works.** Suggestions are submit buttons, so they work. New chat is a form post, so
  it works. Stop is shipped `hidden` and only the script reveals it (the password toggle's
  convention), since without JS it would do nothing.

## Accessibility

- **The log isn't a live region.** Streamed text in a live region would be read out word by word,
  re-read on every re-render, or both.
  - A visually hidden `role="status"` in the panel announces the run instead: "Pedro is answering",
    each tool label, then "Pedro answered", or the failure's lead.
  - The answer itself is then read in the log, where the user can move through it.
  - The streaming message is `aria-busy="true"` until `done`.
- **Focus.**
  - Opening the panel moves focus to the textarea. Close and Escape return it to "Ask Pedro".
  - Focus stays in the textarea after Send, so the next question can be typed straight away.
  - On a lazy load, focus moves to the textarea once the body arrives.
  - When focus is in the panel, Escape closes it, unless the user is composing with an IME.
- **Keyboard.**
  - Enter sends, Shift+Enter is a new line.
  - Enter while `event.isComposing` is left alone, so an IME candidate list isn't sent half-typed.
  - Without JS, Enter is a new line, as in any textarea, and Send is a button.
- **Motion.** The panel slides in over 150ms, and not at all under `prefers-reduced-motion`.
- **Review.** `review_pages.py` gets `/pedro` (its page list must include every `GET` page route),
  plus states that open the panel on a page, against a fake `agent` client.
  - Its contrast and focus checks then cover the panel in both themes at every width.
  - A docked state per `.split` page covers the container-query refactor.

## Brand

- **Gold only on Send and the focus ring** (charter, chapter II). Not "Ask Pedro", not Pedro's
  name, not the messages.
- **Every figure in `--font-mono`.**
  - The renderer wraps number-like tokens in Pedro's text in a mono span: a currency amount, a
    percentage, a signed or decimal figure, a date. Inline code is mono too.
  - Pedro's text gets no gain or loss colour in v1. A colour needs a sign the renderer can trust
    (the contract), and a regex over prose can't supply one.
  - The token pattern is PD-25's, with unit tests for the edge cases ("3 years" isn't a figure,
    "−6.2%" is).
- **Tickers in mono**, by the same wrapping, but without the `ticker` tooltip macro in v1.
  - Every named ticker is a tab stop (UX-1), and a long answer can name many.
  - A follow-up could reuse the macro with `focusable=false`. It's left out of v1.
- **Type.** Fraunces for "Pedro" in the panel header only. Inter for every message.
- **Copy.** Every fixed string above follows chapter IV: plain verbs, no hype, no alarm, no
  exclamation marks. "Unavailable", not "Oops".

## Rendering Pedro's text

Model output is untrusted input (the contract and `agent`'s `prompt-injection` rule). PD-25's
renderer:

- **Markdown with raw HTML off.** Any HTML in the text renders as escaped text.
- **Links.** Only same-origin paths are kept: a path starting with one `/`, through the same
  same-site check as `theme.safe_next`. Any other link renders as its text, with no `href`. That
  includes off-site links, `javascript:` and `data:`.
- **No images.** An image renders as its alt text. An image URL is an exfiltration channel, and
  the CSP's `img-src 'self'` is the backstop, not the plan.
- **Headings become bold paragraphs.** A heading in a chat message would add a heading to the
  page's outline for every reply.
- **Tables.** Recommended allowed, styled like `.positions-table`, with figures in mono. Pedro
  rarely needs one (persona: "light markdown only"). *For the owner to confirm.*
- **Library.** PD-25 chooses it. The workspace has no markdown library yet. markdown-it-py is the
  obvious candidate: CommonMark, `html` off by option, and a link-validation hook. Verify against
  its docs before relying on any of that.

## Page context

With each message the form sends the page the user is on, which `web` turns into `agent`'s page
context (`architecture.md`, "Context assembly"):

- **What's sent.**
  - The route: `/labs`, `/risk`, `/accounts/holdings`, ….
  - Its parameters, from an allow-list per route in `web`, such as the Labs mix and period. Nothing
    off the list is sent.
  - The account filter, if the page has one.
- **Where it comes from.**
  - In the panel: a hidden `page` field. The script sets it from `location` at send time, so a
    section swap that pushed a new URL (UX-10) is reflected.
  - On `/pedro`: the `from` parameter. Without JS there's no swap, so `from` is accurate.
- **What it isn't.** It's descriptive. It says where the user is, and Pedro fetches the figures
  through tools. `agent` validates it and treats an unknown route as "unknown page". It carries no
  figures, so a tampered one can't smuggle any in (the contract).

## Routes this implies (for PD-25)

These are listed so PD-25 starts from them, not as a contract. PD-25 records the final shape in
`architecture.md` and `endpoints.md`.

- `GET /pedro`: the full page.
- `GET /pedro/panel`: the panel fragment, with the latest conversation and the consent state.
- `POST /pedro/messages`: send. It streams for the script and answers `303` for a plain post,
  told apart by an `Accept: text/event-stream` header, which only the script sends.
- `POST /pedro/conversations`: New chat.

## Rejected alternatives

- **An overlay drawer at every width.** It covers the figures the user is asking about.
- **A dedicated `/pedro` page only.** The user leaves the page they're asking about, and
  "available on every page" becomes "linked from every page". It survives as the no-JS path.
- **Pedro in the account menu.** It's one more click, inside a menu for settings and log out.
- **The htmx SSE extension.** GET-only (see "Transport").
- **JSON events rendered in JS.** A second renderer, and its escaping, outside the templates.
- **A conversation list in v1.** More states and more UI before anyone has used one conversation.
  `agent` keeps the conversations, so a list can come later.
- **The log as a live region.** It floods a screen reader.
- **Client-side markdown.** The renderer has to be the one that controls escaping and links,
  which is the server.

## Open questions for the owner

1. **Panel open across navigation**, through the `stakehold_pedro` cookie, or closed on every
   page?
2. **Tables in Pedro's answers**: allowed (recommended) or rendered as text?
3. **The footer's "not advice" wording.**
