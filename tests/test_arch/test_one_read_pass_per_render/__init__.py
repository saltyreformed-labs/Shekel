"""Architecture test: what ONE HTTP render opens, and what it runs twice.

Plan steps **C2-f2d-1** (ledger row **P43**) and **C2-f2d-2** (row **P57**) --
two counts of the same shape, kept in one file because they share one seeding
fixture and one counting discipline, and because the question is the same
question at two levels: what does a render do more than once that it should do
exactly once?

What a read pass is
-------------------

:class:`app.services.balance_at.BalanceContext` pins the three facts every
money answer on a page depends on -- the owner, the baseline scenario, and the
day -- and memoizes what is expensive to derive from them: each loan's ledger
walk, and the owner's pay calendar.  It exists so a render answers those
questions once.

What went wrong, and what it cost
---------------------------------

Nothing enforced that.  Any producer holding a ``user_id`` could call
``BalanceContext.build`` and start a SECOND pass, reading the clock again, and
three did: ``retirement_projection.load_projection_batch`` (a leaf),
``retirement_dashboard_service.compute_gap_data`` and
``retirement_levers.compute_lever_data`` (each via that leaf).  Measured on a
production clone before this leaf: ``/savings`` opened 2 passes and derived the
pay calendar 3 times; ``/retirement`` opened 2; ``/retirement/readiness`` with
a what-if and a lever stepper opened 3.

Two figures published on ONE screen from two passes are two figures computed
against two clocks.  Reproduced on that clone by pinning each pass to a day
either side of a payday: the verdict card's after-tax projected savings read
``$836,398.65`` while the levers card beside it read ``$836,402.83`` -- ``$4.18``
apart.  The two projection AXES differed by one period (517 against 516), which
is not merely a countdown: the contribution lever's annuity factor folds over
the baseline probe's axis, so a one-period difference changes the extra
per-period contribution the page tells the owner to make.  The figures were not
wrong; they contradicted each other, and nothing in the application could
notice.

Why this test is BEHAVIORAL, not a name list
--------------------------------------------

The obvious gate is "these modules may not call ``BalanceContext.build``", and
as a NAME LIST it is the wrong one: it protects exactly the modules someone
remembered to list.  Counting the passes an actual RENDER opens has no list in
it, so a producer wired into one of these routes is covered the day it is wired
in.

**The gate that WOULD be structural is a layer predicate** -- "no module under
``app/services/**`` calls ``BalanceContext.build``", the pass built at the HTTP
boundary and nowhere below it (adversarial design review, 2026-08-16).  That is
not a name list and it is the right end state; **FIVE service modules stand
between here and it**, re-measured 2026-08-27 with
``grep -rl "BalanceContext\\.build(" app/services/`` -- the trailing paren
matters, because the bare name also matches docstrings recording calls that
were deleted.  They are ``calendar_service``,
``investment_dashboard_service/_context``,
``investment_dashboard_service/_orchestrator``, ``loan_recurrence_sync`` and
``tax_report_service``.  ``pay_calendar:C11`` closes them.

**The count went UP at recurrence plan step R7d-c-1 and came back DOWN inside
the same step**, which is worth the two sentences because the intermediate
state is committed.  Its first half had ``period_population`` open a pass
AFTER the write it repopulates, on the ground that a pass taken from above
holds the pre-write calendar and -- from R7d-c-2 -- the pre-write LOAN, which
nothing catches.  The developer refused that as a band-aid (ruling **R-R38**):
the root cause was that ``extend_pay_periods`` did a write and then a
read-dependent write in ONE call, so no caller could get between them.  Its
second half SPLIT the doors, so the route opens the pass between the two
writes, ``period_population`` takes one and builds none, and C11's predicate
needs no carve-out for it.  The paragraph above said FIVE, then SIX, and is
five again.

**This paragraph named EIGHT and three of the names were already wrong**, which
is why it now carries its date.  It listed
``savings_dashboard_service/_orchestrator`` (a door ``C2-f2d-3`` had closed)
and cited ``_orchestrator._pass_for``, a symbol ``git grep`` finds nowhere in
``app/``; and it went on naming both dashboard modules after ``C2-f2e`` retired
them into a package no part of which calls ``build``.  Found by that step's
adversarial design review, in the file the step had just added 281 lines to --
which is the residue rule's own case: editing a file clears that file's stale
claims.  Ledger row **P56** carries the predicate, and asserting it here today
would still fail on the five above, so the gate stays per-render and grows a
render at a time.

**What this file therefore does NOT prove.** It counts read passes, not clock
reads.  That gap was real and is now closed for these renders: until
pay-calendar plan step C2-f2e, ``compute_pension_summary``,
``compute_gap_net_biweekly`` and ``build_employer_salary_basis`` (since
deleted at salary:S3-e-2) each called
``date.today()`` for themselves on the very renders below, so this file could
be green while two cards disagreed across a New Year boundary (ledger row
**P55**, measured at a salary path one year shorter across 2026-12-31 ->
2027-01-01).  All three take the pass's day now, and the gate for THAT claim is
``test_retirement_dashboard_service.TestTheRenderDayOpensTheSalaryPath`` --
still a different question from the one counted here.

One LOAD, and one derivation per plan (C2-f2d-2)
-----------------------------------------------

Sharing the pass left the render loading everything TWICE and deriving the
readiness picture twice.  ``retirement_dashboard_service.compute_gap_data``
computed that picture at the stored retirement date, and
``retirement_levers._probe`` computed it at a shifted one; on a default page
load the lever solver's month-0 probe recomputed, from its own 46 queries,
exactly what the readiness hero had already drawn.  Measured on a production
clone: one page render issued 179 queries of which 86 -- 48% -- were the second
copy, and the two derivations agreed (funded ratio ``0.7463``, required
``$1,120,707.00``, projected after tax ``$836,398.65``) as they had every day
until one of them was edited.

``TestOneLoadPerRender`` counts what a render RUNS rather than what it opens.
Its subject is the four producers that were duplicated -- the gap inputs, the
projection context, the projection batch, and the per-account walk -- and the
walk is the interesting one: its count is NOT one, because the retire-later
lever legitimately probes many candidate retirement dates.  What the assertion
says is that the count equals the number of DISTINCT plans the render asked
about, which is the honest form of "nothing is derived twice".

Test IDs
--------

- ``test_retirement_render_opens_one_read_pass``
- ``test_savings_render_opens_one_read_pass``
- ``test_readiness_whatif_fragment_opens_one_read_pass``
- ``test_the_counter_sees_a_second_pass`` (negative control: the counter
  must be able to fail, or the three above grade nothing)
- ``test_retirement_render_loads_each_input_once``
- ``test_the_page_derives_no_plan_twice``
- ``test_the_load_counter_sees_a_second_load`` (negative control, same argument)
"""
