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
