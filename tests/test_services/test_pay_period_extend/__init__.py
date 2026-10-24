"""Tests for pay-period CRUD slice (c): extend + the repopulation helper.

``populate_periods_from_active_templates`` fills newly-created (empty)
periods with each active template's recurring transactions AND transfers;
``extend_pay_periods`` tail-appends the periods and LEAVES THEM EMPTY.

**The two are one operation the ROUTE composes, and that is ruling R-R38**
(plan step R7d-c-1).  The door used to do both in one call -- a write and then
a read-dependent write -- so no caller could get between them to open the read
pass the generation resolves in, and the populate had to open its own.  A test
here therefore grades ONE of three contracts and says which: the door's own
(what it appends, what cadence it continues, what it refuses) calls
``extend_pay_periods`` alone; the populate half's own
(``TestPopulateFromActiveTemplates``) calls ``populate_in_a_fresh_pass`` alone;
and an assertion about the rows an extend leaves runs
``_extend_andpopulate_in_a_fresh_pass``, which is what the route runs.
``POST /pay-periods/extend`` itself is graded in the
``tests/test_routes/test_pay_period_admin/`` package.

Because a pay period is the spine of every financial number, the extend
happy-path test asserts three disciplines: structural invariants
(Discipline 1, ``assert_pay_period_invariants``), hand-computed as-of
balances in both the retained and the new window (Discipline 2), and the
production integrity checker passing (Discipline 3).  The disciplines are
named in ``docs/plans/historical/implementation_plan_pay_period_crud.md``, a
historical record that governs nothing.
"""
