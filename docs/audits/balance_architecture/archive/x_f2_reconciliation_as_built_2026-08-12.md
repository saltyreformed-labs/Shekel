> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# X-f2 as built: the true-up is a reconciliation

It records how the true-up-is-a-reconciliation cluster came to be built. Cite it for that; never read
it as a live plan or as a statement of the current state.

Condensed out of the balance README on 2026-08-12 under conventions rule 5 -- the only legal way back
under a document's cap is to archive a COMPLETED span, one line per step, and this span is complete:
every step below is shipped and `X-f2` ticked with its last leaf.

## The span: X-f2, the true-up is a reconciliation (R-DH (f)'s second half)

**The DECOMPOSED parent** (`R-EU` / `R-EV` / `R-EW`, 2026-08-09), one session's work per leaf, ticked
with `X-f2-c3`. **The developer's existing workflow already IS this loop** -- read the bank, type the
balance, tick what cleared -- and only the RECORDING changed.

| step | commit | what it did, and what it closed |
|---|---|---|
| **X-f2** | `9afd53f1` | The parent. `X-f2-a` (`397ce36e`) and `X-f2-b` (`a41b5ebf`) are condensed in `phase_x_as_built_2026-08-04.md` 1c |
| **X-f2-c** | `9afd53f1` | The OUTSTANDING SET, widened (`R-EW`): the panel offers everything the statement can settle and a tick stamps the STATEMENT date, which is **N-172**'s churn. Three leaves |
| **X-f2-c1** | `24701c1d` | The module home and the grouped shape. Closed **N-216**; opened **N-217**, **N-218** |
| **X-f2-c2** | `d23b55fd` | The TRANSACTION twin (`R-FA` / `R-FB` / `R-FC` / `R-FD` / `R-FF`): the envelope's close and bills, income included, settled through the grid's own verb on the STATEMENT day. Closed **N-222**, **N-223**, **N-227**; opened **N-225**-**N-233** |
| **X-f2-c3** | `9afd53f1` | The TRANSFER shadows (`R-FA`): `settle_transfer` is the named verb every settle door reaches, so both legs and the parent move together and a tick settles the leg on the OTHER account. Closed **N-225**, **N-226**, **N-231**, **N-232**; opened **N-255**-**N-259** |

## The two rules a LATER step must still obey

These are carried FORWARD rather than archived, because a live step depends on each. Both are also
stated at the step that owes them, which is where a reader meets them:

* **`9325fe6a`'s offer rule** (from `X-f2-c2`): a row is offerable only when everything it would book
  moved by the statement day, so an envelope holding a later purchase is not offered. Any LATER arm
  added to the panel keeps it.
* **Why `R-FH`'s column move is not in `X-f2-c3`** (measured, not conservative): the freeze cannot
  write a row's own amount until the schema can say whether that amount is the row's or is derived, or
  `loan_payment_service._manual_shadow_amount` reads its own output and a settle / revert / settle
  cycle compounds the standing extra (`$1,599.10` -> `$1,699.10` -> `$1,799.10`). That is finding
  **N-259**, owned by the amount model's freeze leaf.

## The measurement the panel was sized by

Taken over production's 53 assertion DAYS: **34 would have had something to tick, 100 rows worth
$23,910.04, OF WHICH 8 rows / $5,442.89 are transfer shadows.** The OF-WHICH is load-bearing --
reading it as "plus" double-counted the shadows at $29,352.93 until it was re-derived from
`shekel-prod-db` on 2026-08-10, which is why the sentence is written this way wherever it appears.

## The settle doors and the amount cache, measured

The three steps that ran beside this span and are condensed to one line each in the live document.
Their measurements are here because they are the record of what a shipped step MOVED, and the live
entry's job is to point at what a later step must obey.

| step | commit | what it measured |
|---|---|---|
| **X-aq** | `9cabc206` (fixed at `c4932746`, `41f678e5`) | A settle books the freshest derivation of the row's own amount, in the VERB, so all its doors agree (`R-FE`); `c4932746` moved that write to the CACHE (`estimated_amount`) and off the human's column (`R-FH`). Figure-neutral on production (0 rows, `$0.00`) and **51 rows / `$4,897.50`** on a clone carrying one un-regenerated 3% raise -- the whole point of the step, in one number. Opened **N-224** |
| **X-as** | `ffb9514c` | A tax year resolves to the latest CONFIGURED year's rules at or before it, PER CONFIG KIND, never to the clock's year -- which on 2027-01-01 resolved to NOTHING and dropped **$8,460.50** of withholding out of the projection (`$10,914.93` with that day's top-up), because the engine reads a missing `fica_config` as zero Social Security. Opened **N-235**, **N-236** |
| **X-ap** | `8726beca` / `32db32af` / `616cf157` | THE THIRD SETTLE DOOR, which `R-FA`'s own text missed by naming two. Measured before and after on production: row 2231 *Gas*, an `$80.00` budget carrying one `$48.98` purchase, booked **`$80.00`** through the full-edit dropdown and `$48.98` through Mark Paid -- **`$31.02` of spend that never happened**. The first two commits moved no money: a ROUTE stopped deciding what a status change means, then **N-233** / **N-229**. Closed **N-219**, **N-230** (`R-FJ`); opened **N-244**-**N-247** |

## Extended 2026-08-13: the anchor half's parent record

The X-f3 decomposition took the live document to its cap, and rule 5 archived the rest of this
span. Every step below already had a record HERE or in `phase_x_as_built_2026-08-04.md` except
**X-an**, whose leaves were condensed on 2026-08-09 while the parent stayed in the index; its own
line is this table's reason for existing.

| step | commit | what it did, and what it closed |
|---|---|---|
| **X-an** | `3d3f0ef5` / `549015c0` | A loan payment is history from the day its money moved (`R-EK`) -- the DECOMPOSED parent, complete at two leaves (`X-an-a`, `X-an-b`, condensed in `phase_x_as_built_2026-08-04.md` 1b). Closed **N-187**, **N-196**; opened **N-207**-**N-211** |

**The condition `phase_x_as_built_2026-08-04.md` 1b stated for keeping the parent in the index --
"live sentences cite it by id" -- was OVERRIDDEN rather than falsified.** The citations are still
there (`steps.md`, `conventions.md` rules 13 / 16, `tools/plan_gate/`, the recurrence plan), and
conventions rule 10 is why that is legal: ids are append-only and a citation resolves through the
archive. What the condition was really protecting is the parent's own `id / commit / what it
closed` line, which is the row above.

