# web copy

How the pages' words are written and where the explanations live: the glossary, and the
rules for intros and notes. Split out of [`ui-architecture.md`](ui-architecture.md) for size
before UX-9, with its Glossary entry moved here verbatim. It is part of the same document, so
`.claude/rules/architecture.md` applies to it: a change to anything described here updates this
file in the same change.

## Key decisions

- **Glossary: one page, linked from each figure's label.** The holdings tables show two
  percentages side by side (unrealized return and total return; a third, growth, has since been
  removed), and nothing on the page said how they differ. `/glossary` defines every figure the holdings pages and the overview
  show, and (LB-6) the Labs terms and (RK-12) the risk measures and (RK-14) the benchmark and its four measures, one `<dt id>` per term. Each column header and tile label on `/`, `/portfolio` and
  `/accounts/holdings` links to its entry through the `term` macro
  (`partials/_term.html`). The link inherits the label's colour with a dotted underline, so a
  row of headers doesn't turn into a row of accent links. At phone width a stacked table hides
  its `<thead>`, and its `data-label`s are CSS text that can't be links. So each holdings card,
  and the overview's accounts card, whose rows have no headers, also carry one plain "What these
  figures mean" link. The chart's view buttons aren't linked, since a link inside a button is
  invalid. *Rejected: `title` tooltips*, which don't show on touch screens and which screen
  readers announce inconsistently. *Rejected: an explanation under each table*, which would be
  three copies to keep in step. *Known cost:* an anchor renamed in `glossary.html` breaks its
  links silently in a browser. Each page's tests fetch `/glossary` and check every link lands
  on an entry.
- **Copy density (UX-9): one sentence per intro, the method in the glossary.** Each page grew
  its notes chunk by chunk, so intros ran to four sentences and several notes repeated their
  glossary entry almost word for word. The rules now:
  - *An intro is one plain sentence* that says what the section shows, with a `term()` link to
    its own glossary entry where the page shows figures: ex-post, ex-ante, what if
    (`#what-if`), candidate ranking (`#candidate-ranking`), look-through, the Labs backtest and
    buy and hold. Form and admin pages get one sentence and no link.
  - *A method note is one sentence.* What it cut (why the ranking never orders by Sharpe, how a
    regime at the edge of the history is measured, what each FX column is) lives in the entry
    its term links to. UX-9 added `#what-if` and `#candidate-ranking`, the only cut detail
    the glossary didn't hold.
  - *A chart's key stays beside it.* The Sharpe band and a break in the drawdown line say how
    to read the picture, so they stay as clauses of the note's one sentence, like the heatmap's
    shade key.
  - *A note shows once per page.* "Hover a figure…" is on the what-if's as-of line when its
    table shows, else on the ranking's, and the ranking's swap re-renders with the same rule.
    The holdings pages' empty return history repeats the freshness note only when the page has
    no as-of line to carry it. Add a ticker says how long prices take once, beside the button.
  - *"Historical" stays in every block (R15)*, as one word in its sentence rather than a
    ": historical figures, not a forecast" clause. A swapped region or a deep-linked block must
    carry the word itself, so it isn't said once per page. *Rejected: once per page*, which a
    UX-10 swap or an `#anchor` would land past.
  - *Not changed:* the marker legends, each the key to its own table; banners, which give a
    section's state and fail on their own; the view modules' reason strings and notes, which are
    data; and the new-transaction amount hints, which `transaction_form.js` shows one at a time.
