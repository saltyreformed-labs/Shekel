> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-bx` as built: the settled-only accessor refuses an unsettled row (archived 2026-09-11)

**Condensed out of the balance README under `docs/plans/conventions.md` rule 5 on 2026-09-11**
(developer's call under rule 4). Carried VERBATIM and WITHOUT re-verification. Its closing
sentence quoted a MEASUREMENT (`_amount_source.py` at 971 of pylint's 1000-line cap, three
modules on it, two at 999) that was true when written and is a command now: `wc -l` the module.

* [x] **X-bx** `f7b9e094` -- closed **BAL-465**: deleted the fall-through by which
  `owned_contribution` read `estimated_amount` by hand, renamed it `settled_contribution`, and moved
  `own_figure` into `_amount_source` as the private `_own_figure`. **Ruling R-BAL5 superseded this
  entry's own remedy** -- the fall-through is DELETED, not routed to the resolver, because these
  readers ask what a row's money DID. **A LATER STEP MUST OBEY**: `_amount_source.py` is at 971 of
  pylint's 1000-line cap, and three modules sit exactly ON it with two more at 999.
