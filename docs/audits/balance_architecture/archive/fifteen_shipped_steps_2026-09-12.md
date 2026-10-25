> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Fifteen shipped balance steps, archived out of the plan of record (2026-09-12)

**What this is.** The fifteen one-line `* [x]` entries that left `../README.md` section 5 on
2026-09-12 under conventions rule 5, WITH their `docs/plans/steps.md` rows, to buy the room
`X-bi-1`'s tick needed: the README stood at **1308 of its 1,330-line cap against a 20-line headroom
floor -- two lines of room** for a tick that nets eight (a six-line specification becomes a
three-line tick and the new leaf `X-bi-1b` takes eleven). The developer ruled 2026-09-12 that every
fully shipped span leave, and rule 12 is why the ROWS leave too: the gate grades the index and the
arc document in both directions for a SHIPPED row as for an open one, so a one-line stub was the
smallest thing the 2026-09-01 pass could leave and these entries were ALREADY that stub -- nothing
in them could be condensed further, only removed, which is the older passes' pattern (`X-f1`'s
fourteen leaves, `X-an`, `X-au-a`) and the one rule 13 already names: *X-f1's fourteen leaves have
already left the index*. **The COMMIT is the record for every one of them**; read the code each
shipped, not this file.

**Why these fifteen and not the twenty-six the developer named.** Twenty-six fully shipped spans
were ticked in the README. Seven are NAMED in a live row's `blocked by` cell and a row may not
leave the index while a cell points at it (rule 13: every key names a real step) -- `X-au-k`
(`recurrence:R7d-c-2`), `X-au-d` (`X-au-l`), `X-au-e` (`X-bp`, `X-au-l`, `bank_import:X-f6c`),
`X-au-f` (`X-au-m`, `X-bi-6a`, `X-bi-6`, `X-au-l`, `R7d-c-2`), `X-au-h` (`X-bq`), `X-bv` and `X-ca`
(both `X-bv-2`); they keep their one-line entry and their row. Three carry a **"A LATER step must
obey"** clause -- `X-bl-2a`, `X-bl-2b`, `X-bx` -- which rule 5's second condition forbids archiving
without restating, and restating costs the lines back. `X-bz`'s second line is a LIVE operational
fact about what a rollback does (the 2026-09-10 pass kept it for that reason). `X-f1` was archived
on 2026-08-26 and PUT BACK the same day (`credit_card:CC3b`'s blocker cell names it and a plan-gate
control derives its specimen from that cell); `X-l` is one step under three names, and rule 11
keeps its row. `X-au-f-2` is the SPECIMEN a plan-gate control is anchored on
(`tools/plan_gate/test_shipped_against_git.py`, *the control fires on the specimen this arm exists
for*), so its row may not leave until that control is re-anchored -- the same reason `X-f1` went
back. The remaining fifteen carried nothing live: their opened and closed findings are
rows in `docs/plans/ledger.md` with live owners, their rulings rows in `docs/plans/rulings.md`, and
their as-built records are already in this folder where an entry names one.

**Every row here is carried WITHOUT re-verification** (rule 5's third condition): each is reproduced
from the entry and the index row it replaces, and no claim in it was re-measured on 2026-09-12. The
commit each names is the thing to read.

**Two containers keep leaves only here.** `X-cf` stays open in the index (`X-cf-3b`, `X-cf-4`) and
ticks with its last leaf; its first three leaves' commits are below. `X-au-f` stays as a one-line
entry because live cells name it, with `X-au-f-2` under it; `X-au-f-1` and `X-au-c3` are below.

## The fifteen entries, reproduced from `../README.md` section 5

* [x] **X-au-c** `3d1379d1` -- the amount model's SEAM, ticked with its last leaf `X-au-c3`. THREE constraints it leaves on later steps (the resolver, the `cash_ledger` name, the STATUS-free amount rules) are in `archive/seven_shipped_pointers_2026-09-05.md`, with `archive/x_au_c_as_built_2026-08-26.md`.
* [x] **X-au-c3** `3d1379d1` -- a settle RECORDS what moved rather than refreshing an amount. `archive/eight_shipped_steps_2026-09-01.md`.
* [x] **X-au-f-1** `ce8bf485` -- every parent-transfer render site takes the amount model's answer; byte-identical BY CONSTRUCTION. Closed **N-452**; opened **BAL-476**. Record: `archive/x_au_f_1_as_built_2026-09-09.md`.
* [x] **X-bl-1** `e0e257a7` -- a cutover's control can FAIL: seven instance-distinct perturbations of the sources a row's rule names, eight mutations firing it. Closed **N-445**. Record: `archive/x_bl_1_as_built_2026-09-09.md`.
* [x] **X-aw** `078077db` -- a paycheck's gross is a RATE (**R-HW**), so **N-239** died by construction. Closed its horizon half; opened **N-390** and **N-391**. Record in `archive/four_shipped_steps_2026-08-30.md`.
* [x] **X-bh-1** `b955d0c8` -- the paycheck engine reads the owner's CALENDAR, so **D25**'s narrow context is unrepresentable rather than forbidden in prose. Opened **N-394**, **N-395**, **N-396**. Record in `archive/eight_shipped_steps_2026-09-01.md`.
* [x] **X-bh-2** `49fdfb91` -- the rhythm runs BACKWARD too (**R-IA**), bounded by a stored registration; **`NULL` means NOT STATED** (**R-IF**). Closed **N-390** and **N-396**; opened **N-398**, **N-399**. Record in `archive/eight_shipped_steps_2026-09-01.md`.
* [x] **X-bu** `142f64cb` -- closed **BAL-462**: `row_valuation.owned_amount` deleted and folded into `_amount_source._own_answer`, growing one reader under **R-BAL4**. Its obligation on `X-bx` was DISCHARGED there (`f7b9e094`) by deleting the copy rather than routing it.
* [x] **X-cf-1** `8fac9e6f` -- built `generate_row_of(template, period)`, which CALLS the engine into one paycheck, and moved the shared fixtures onto it.
* [x] **X-cf-2** `b3e51806` -- the model, script and util suites; its review found a 60-day paycheck holding THREE monthly firings two months a year, pinned by `definition_firing_twice_in_a_paycheck`.
* [x] **X-cf-3** `641801a3` -- the service suites (23 census sites, 8 hand-dated); two series-versus-scalar controls made real, the override-sibling pair kept UNDATED so the one-statement flip stays graded.
* [x] **X-bd** `39935763` -- every route in the sweep is its OWN pytest item. Closed **N-364**, whose diagnosis it measured FALSE; opened **N-387**. Record in `archive/four_shipped_steps_2026-08-30.md`.
* [x] **X-be-2** `167aab8d` -- a test SAYS what world it starts in. Closed **N-387**, whose read-only premise it measured FALSE; opened **N-388**. Record in `archive/four_shipped_steps_2026-08-30.md`.
* [x] **X-be-3** `0aa2cc80` -- the sweep grades EVERY GET route and carries no list of the ones it does not; coverage is an equality against `url_map`. Closed **N-388**. Record in `archive/eight_shipped_steps_2026-09-01.md`.
* [x] **X-am** `7b0ddae8` -- the `Settled` ARCHIVE is DELETED (**R-HA**, which carries what `CC3b` owes). Closed **N-177**; as-built in `archive/x_am_as_built_2026-08-27.md`, entry in `archive/four_shipped_steps_2026-08-30.md`.

## Their fifteen index rows, reproduced from `docs/plans/steps.md`

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-au-c | -- | The DECOMPOSED parent of the amount model's SEAM, split into three leaves 2026-08-12: the schema and the declaration, the readers, then the freeze and its inverse; it ticked with its last leaf. | SHIPPED | `3d1379d1` | -- |
| balance | X-au-c3 | -- | A settle RECORDS what moved instead of refreshing an amount: `settled_amount` + `settled_basis_id` + `settled_on` are one record the status seam alone writes, a revert releases the ASSERTION and KEEPS the fact, and both full-edit popovers correct a figure IN PLACE. Closed **N-241**, **N-242**, **N-257**, **N-259**, **N-265**, **N-282**, **N-298**; opened **N-301**-**N-304**. | SHIPPED | `3d1379d1` | -- |
| balance | X-au-f-1 | -- | Made every parent-transfer render site take the amount model's answer rather than the `transfers.amount` column, and `render_transfer_cell` made that context ONE definition where six sites had assembled it by hand. Byte-identical BY CONSTRUCTION: no writer in `app/` declares a transfer derived, so rule 1 returns that same column. Closed **N-452**; opened **BAL-476**. | SHIPPED | `ce8bf485` | -- |
| balance | X-bl-1 | -- | Rebuilt the amount resolver harness's invariance control so it CAN fail (**R-JF**): seven INSTANCE-DISTINCT perturbations of the SOURCES a row's rule names, each asserting that exactly the rows declared to that source move by exactly it while every other row holds, where the pass it replaced skipped all 934 derived rows and counted the skip as evidence. Closes **N-445**. | SHIPPED | `e0e257a7` | -- |
| balance | X-aw | -- | Made a paycheck's gross a RATE rather than a share of a year (**R-HW**): the salary over the owner's paycheck count, one figure per salary segment, so the period LIST that decided which paychecks took a rounding-residue cent is gone and with it the defect that re-priced settled paychecks as the schedule grew. Closed **N-239**'s horizon half and opened **N-390** and **N-391**. | SHIPPED | `078077db` | -- |
| balance | X-bh-1 | -- | Made the paycheck engine read the owner's whole pay CALENDAR off its `PayrollBasis`, rather than an `all_periods` sequence every window and one-period sample satisfied, and collapsed its four calendar judgements onto two producers the analytics month card shares in its bounded form (**balance:R-IB**). Closed **N-390**'s type half; opened **N-394** and **N-395**. | SHIPPED | `b955d0c8` | -- |
| balance | X-bh-2 | -- | Made the payday RHYTHM run BACKWARD as well as forward, bounded by a stored `budget.pay_schedule.history_opens_on` two doors ask for, so an owner who says when their paychecks began is counted from their real first one and an owner who says nothing is counted from the record, unchanged (**R-IA**, amended **R-IF**). Closed **N-390** and **N-396**; opened **N-398**, **N-399**. | SHIPPED | `49fdfb91` | -- |
| balance | X-bu | -- | DELETE the public `owned_amount` accessor and fold its body into the resolver's OWN arm, so a settled row's plan has ONE producer and the `AmountUnresolvable` that 500'd `/analytics/spending` on production data becomes unrepresentable rather than caught. Closes **BAL-462**. | SHIPPED | `142f64cb` | -- |
| balance | X-cf-1 | -- | Built `tests._test_helpers.generate_row_of(template, period)`, which CALLS the recurrence engine into exactly one paycheck and hands back what it wrote, and moved the shared fixtures (`seed_full_user_data`, `seed_entry_template`, `create_envelope_txn`, statement_match's `a_transaction`) onto it. | SHIPPED | `8fac9e6f` | -- |
| balance | X-cf-2 | -- | Moved every hand-built `Transaction(template_id=...)` in `tests/test_models`, `test_scripts` and `test_utils` onto the engine's row, the builders the census cannot see included, and pinned the two-rows-in-one-paycheck cases on `definition_firing_twice_in_a_paycheck` after its review measured a 60-day paycheck holding THREE monthly firings for two months of every non-leap year. | SHIPPED | `b3e51806` | -- |
| balance | X-cf-3 | -- | Moved the service suites' 23 census sites and 8 hand-dated ones onto the engine's row, made two series-versus-scalar controls that passed under their own mutation real, and kept the override-sibling pair UNDATED so carry-forward's one-statement period-and-flag flip stays graded. | SHIPPED | `641801a3` | -- |
| balance | X-bd | -- | Gave every route in the `url_map` sweep its OWN pytest item so the per-test wall clock stops containing the route table at all, rather than re-cutting arms that only divide it, and enumerated the routes without a database so collection cannot fail. Closed **N-364**; opened **N-387**. | SHIPPED | `39935763` | -- |
| balance | X-be-2 | -- | Gave the suite a way to say WHAT WORLD a test starts in: a named seeded start state, built ONCE per worker and frozen into a snapshot every declaring test still takes its OWN private clone of, so the `url_map` sweep stopped rebuilding one identical world 236 times without weakening what isolates one test from another. Closed **N-387**, whose premise it measured FALSE; opened **N-388**. | SHIPPED | `167aab8d` | -- |
| balance | X-be-3 | -- | Widened the world to a salary profile, a transaction, a transfer, a goal, BOTH template kinds and a pension, then deleted `_UNREACHED_RULES` and the skip branch feeding it, so *every GET route is graded* became an equality against `url_map` rather than a list of the ones that were not: all 17 rules it had never requested answer, and **none 5xx**. Closed **N-388**. | SHIPPED | `0aa2cc80` | -- |
| balance | X-am | -- | Deleted the `Settled` status -- the terminal ARCHIVE, reachable from one dropdown, carrying zero rows in any snapshot or audit trail, and refusing every act that CORRECTS a row while permitting the DELETE that destroys it -- and ruled that no state may be both reachable and absorbing (**R-HA**). Closed **N-177**. | SHIPPED | `7b0ddae8` | -- |
