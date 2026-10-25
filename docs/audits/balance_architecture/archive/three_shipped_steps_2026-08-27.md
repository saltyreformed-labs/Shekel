> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Three shipped balance steps, archived out of the plan of record (2026-08-27)

**What this is.** The three `* [x]` entries that left `../README.md` section 5 on 2026-08-27 under
`docs/plans/conventions.md` rule 5, to buy the room `X-f3c`'s five-leaf decomposition needed. The
README stood at **983 of its 1,000-line cap under a 20-line headroom floor**, and rule 4 forbids
raising a cap when it binds. **The COMMIT is the record for every one of them**; read the code each
shipped, not this file.

**Why these three and no others.** Every one is SHIPPED, and every one had already been condensed to
a single line, which is what rule 5's archive reduces a span to -- so there was nothing left to
shrink and the only remaining move was removal. They were also the only three shipped entries in the
document that **no `blocked by` cell names**: `X-f1`, `X-l`, `X-au-c3`, `X-ad-a`, `X-i3-a` and `X-i4`
are each cited as a satisfied blocker by a live step, and archiving one would have deleted a
recorded dependency to save a line. That is the same trade `five_shipped_steps_2026-08-26.md`
recorded when it put `X-f1` back.

**Rule 5's second condition -- no live sentence may depend on an archived one -- holds for all
three, and was checked rather than assumed.** `X-f3b` is named by no live entry in the document
(the `X-f3` parent and its `X-f3c` leaves each state their own subject); `X-i3` is named by none.
`X-i3-b` was in the first cut of this file and was PUT BACK: `X-i5`'s live specification says the
step "keeps `X-i3-b` a NARROWING rather than a deletion", which is a constraint on unbuilt work
rather than a citation of how something came to be.

## The three entries, verbatim

* [x] **X-f3b** `38ffd87b` -- a purchase carrying a recorded bank posting day is a cash movement of
  its OWN (**R-FM**, refined by **R-FR**). Closed **N-274**, **N-286**, **N-288**; opened
  **N-290**-**N-292**.

* [x] **X-i3 THE SNAPSHOT** `1feb0930` -- the DECOMPOSED parent, ticked with `X-i3-b`, its last leaf
  (ruling **R-GU**). Its four members are as built in `x_i_binding_as_built_2026-08-26.md`.

**And one correction to the record this file leans on.** `x_i_binding_as_built_2026-08-26.md` was
written while `X-i3-b` was still open and says so ("whose remaining leaves (`X-i1`, `X-i2`,
`X-i3-b`, `X-i5`, `X-i6`) are LIVE"). `X-i3-b` shipped at `1feb0930` the same day. That file is
archived and is not edited; the fact is recorded here instead, which is where a reader arriving from
its own banner is told to look for what is true now.

## What still points here

Nothing. `X-f3b`'s and `X-i3`'s rows left `docs/plans/steps.md` in the same commit, which is what
rule 12 requires of an archive in both directions.
