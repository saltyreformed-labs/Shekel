# The verification standard

**One copy of what "done" means for a step in any arc.** It lived in the balance README's section 7
and, in a shorter and not-quite-equal form, in the credit-card plan; the recurrence and pay-calendar
arcs had none at all, which is how the two arcs at the front of the queue came to be held to nothing
written down. That is the same denormalization `lessons.md` exists to remove.

**This is the ORACLE discipline, not the release checklist.** What every commit owes -- full suite,
`pylint app/` with the full `--fail-on` set, migrations tested in both directions -- is
`CLAUDE.md`'s Definition of Done, and restating it here would be a second copy beside no reconciler.
This document answers the question the Definition of Done does not: how do you know the number is
RIGHT.

## The standard

1. **The baseline must not move** unless the step's design says it moves, in which case every moved
   number is individually explained and signed off.

2. **Oracles are exhaustive and independent.** Every day, every shape; never a sample; never two
   producers that share code proving each other. **Never a producer as its own oracle.** The fold is
   the reference.

3. **Ask of every harness: can it SEE the code under test?** Twelve exist because each is blind
   where the next one looks, and a harness blind to a step reports byte-identical -- a free pass
   that reads as proof. **Plan step X-f3d is that failure worked**: it re-points a balance
   assertion's COUNTER leg, `verify_balance_baseline` came back byte-identical over 9 accounts and
   5,978 daily points, and the answer graded nothing -- `app/services/balance_at/` issues no query
   against the posting tables at all, so it cannot move under ANY posting-ledger change. The harness
   that step needed did not exist; it is the sixth row.

   | harness | what it can see |
   |---|---|
   | `tests/manual/verify_balance_baseline.py` | Every figure the seam can answer about every account in a database. Run before and after, `diff` the blobs. DETERMINISTIC, and a REGRESSION check rather than a proof: two identical figures can both be wrong. Every figure is read at the seam's default `as_of`, so a step scoped to a pinned historical `as_of` moves nothing in it |
   | `tests/manual/verify_savings_producers.py` | Above the seam, where the first is blind: a producer package, a serializer or a template |
   | `tests/manual/verify_anchor_surfaces.py` | The anchor surfaces both others miss: the grid header's figure and "as of" caption, the reconcile panel, the dashboard balance section, the pulse hero, the savings dashboard including the ARCHIVED drawer, Property market value / home equity, and the retirement seeds. A producer that raises is RECORDED rather than fatal -- a probe that dies on account 3 has silently stopped covering 4 through 9 |
   | `tests/manual/verify_render_surfaces.py` | 108 authenticated routes, status + body size (plan step C2-c). It cannot see a FIGURE; what it catches is a surface that stopped rendering at all, which the three above are blind to because they call producers rather than routes |
   | `tests/manual/verify_statement_baseline.py` | Every figure the two confirmed-ledger STATEMENTS render (plan step X-f3d), which every harness above is blind to: the income statement for each pay period and each populated calendar month and year, the balance sheet at both ends of the attributed span and each period end, section by section and line by line, with the whole two-part tie-out. Windows are enumerated from the ledger's own days, never sampled |
   | `tests/manual/verify_projection_axis.py` | Every figure the forward PROJECTION axis decides (plan step C2-e): the /retirement gap, readiness and both lever solvers, the /savings Horizon's bands and milestones, the /investment growth chart at three slider positions, and the Property equity chart. The first three harnesses are all BELOW or BESIDE these producers. It states its own gate rather than assuming byte-identity: the axis is anchored on the owner's paydays, so it diffs clean only when their cadence is 14 AND the read day opens a period, and it prints both facts in its header so a legitimate move is not read as a regression |
   | `tests/manual/verify_investment_cutover.py` | Every figure `/investment` publishes (plan step C2-f2c), through all THREE of the package's public entries -- the dashboard first paint, the growth-chart fragment at three slider positions with and without a what-if, and the balance hero cell -- for every ACTIVE account rather than the investment ones alone. `verify_projection_axis` above it reads the chart and never the cards or the hero; `verify_reader_baseline` reads the dashboard and neither of the others. It also dumps `derived_vs_stored` per period, which is what says whether the database under it can express the disagreement the diff would be measuring |
   | `tests/manual/verify_retirement_render.py` | Every figure the `/retirement` PAGE and its what-if fragment publish (plan steps C2-f2d-1 and C2-f2d-2; written as `verify_retirement_pass_cutover.py`, first committed under this name) -- the readiness verdict, both levers, the per-account projections, the assumptions rail's blended return and the what-if at an override -- plus `/savings` and the budget dashboard's TRACKS section, which is the one production caller that hands one read pass to both narrow producers. The page is the readiness verdict beside the lever card, which `verify_reader_baseline` (the savings dashboard and the retirement projection) does not call. It captures the figures TWICE -- once with a pass per producer and once with one shared -- because the first alone reproduces the topology C2-f2d-1 replaced. It also counts what a render RUNS, read passes and producer calls, which are expected to move, under their own keys so a legitimate move is not read as a regression |
   | `tests/manual/verify_dashboard_cutover.py` | Every figure the BUDGET DASHBOARD publishes (plan step C2-f2e): the pulse region WHOLE, the position tracks and the anchor editor's revert fragment, for every user. `verify_period_window_cutover` dumps the pulse region and neither of the other two; `verify_anchor_surfaces` overlaps the hero's `last_updated_date` alone; `verify_render_surfaces` can tell that `/` still renders and nothing about what it says. It resolves the three producers by CAPABILITY (`hasattr(dashboard_service, "resolve_section")`) rather than by name, so one file runs on both sides of a step that moved all three |
   | `tests/manual/verify_generation_pass.py` | Every ROW a generate pass writes, through all three doors that reach one: the extend path (`pay_period_admin.extend_pay_periods` then `routes._period_population.populate_new_periods`, which is the composition ruling **R-R38** split -- the harness dispatches on whether the tree HAS that module, so one file runs both shapes), the whole-schedule generate the create / unarchive / salary / template-edit routes run, and the carry-forward PREDICTION. Every harness above it reads a PRODUCER or a RENDER, and generation is neither -- it is the WRITE whose output they all later read, so a change to which rows exist is invisible to all nine (plan step R7d-c-1). It names a period by its PAYDAY and a carry-forward plan by its row's name and figures, never by an id: a sequence is not rolled back, so an id-bearing dump reports moved lines between two runs of the SAME code -- measured at 28 |
   | `tests/manual/verify_loan_bound_at_generation.py` | Every ROW a generate pass writes for a definition paying into a LOAN, which `verify_generation_pass.py` is blind to: its 20-period extend never reaches a payoff, so the bound cannot be observed. Five doors on a STAMPED production clone extended 80 periods past the nearer payoff (plan step R7d-c-2; re-measured on R16-b-2's tree 2026-09-11: live doors byte-identical, D4 35/65 -> 32/32): extend past the payoff and then the FIXED POINT (a fresh read, the maintain pass, a second generate), regenerate from the first not-yet-started period, a planted true-up with the column left behind followed by the maintain pass, a planted second definition bound past the loan's life, and the loan's own payment with its column planted one installment early (D35's shape). Dev's snapshot is UNSTAMPED, so it duplicates every row; the clone must be production's Re-measured 2026-09-11 on R16-b-2's tree with generation round the door as the base: the live doors byte-identical, the planted second definition 35/65 -> 32/32 rows to `2028-11-22` -- the sum reaches the ROWS only through generation taking the door |
   | `tests/manual/verify_loan_plan_sum.py` | Every figure the forward LOAN PLAN publishes -- the payoff, every payment and charge, a 36-month balance grid -- for both loans on a STAMPED production clone, plus EIGHT PLANTED doors: five where the summed tier must differ from the one it replaced (plan step R16-b-2): a second definition into the Mortgage (D47), the reset door's hole (D46), an ad-hoc extra beside an unanswered installment, a soft-deleted future row (D53), and a projected extra in a seeded month (D54); two delinquent loans (R16-c-1); and a settled payment due off its contractual day, the only control for the loan page's history card and schedule (R16-c-2). The BASELINE is the `$0.00`-live claim and must be byte-identical; each door names the direction and the size of its move. Every door rolls back, and a door the app refuses prints `# DOOR n REFUSED` and the run goes on: the EXIT CODE is how many doors were refused, so only 0 means every door graded (finding plan#1859) |

   **Use `git worktree` for the HEAD side, never `git checkout`.**

   **A harness that a step's own change makes uncompilable on the HEAD side is not a harness.**
   Write it against what BOTH sides can answer -- `dict.get` for a key the step adds, positional
   keys where the step changes an identity -- or the diff reports every line moved and grades
   nothing.

4. **Every guard gets a negative control that is SHOWN to fire.** A guard whose control does not
   fire is not a guard.

5. **The fixture matrix must contain the shape the feature exists for** -- a paid loan, an
   off-schedule payment, a delinquent loan, a card past its grace.

6. **The suite has two clock gates.** CI runs `TZ: Pacific/Kiritimati`, so a `date.today()` /
   `display_today()` mix fails there, and a weekly sweep runs the suite at a leap day, both sides of
   a year boundary, a month end and the first of a month. **Read `docs/test-suite-clocks.md` before
   writing a fixture that touches an anchor, an assertion instant or a due date.**

7. **Green gates are necessary, never sufficient.** A `$197,049.32` defect passed pylint 10.00 and a
   7,387-test suite. Live-render the affected surfaces against the dev clone before landing anything
   that moves a figure a screen shows.

8. **No uncited claims in a planning document.** Anything stated as fact about the code carries its
   own commit hash or was verified on its write date; when you edit a file, re-verify what you
   touch.

## What is arc-specific and stays in the arc

The standard above is what every arc owes. A step whose acceptance needs a WALK through the app --
the credit-card arc's end-to-end run from creating a card to the next payment shrinking, for
instance -- states that walk in its arc's document, because it is one arc's proof rather than a rule
about proofs. The distinction is the planning conventions':
**merge what shares KEYS, split what shares only a READER.**
