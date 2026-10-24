"""Tests for pay-period CRUD slice (a): the bounded full reset.

``reset_pay_periods`` is the highest-risk operation in the feature: it
deletes the user's ENTIRE schedule -- including every historical period,
which regenerate can never touch -- and rebuilds it from a corrected start.

**It no longer re-anchors anything, and the FK it used to defer is gone**
(rulings R-EH and R-EO, plan step X-f1c3c).  This docstring described the
old machinery: the account carried a ``current_anchor_period_id`` and the
assertion a ``pay_period_id``, so the reset had to delete the anchor period
and re-point every account inside ONE transaction with the FK deferred to
commit (``SET CONSTRAINTS ... DEFERRED``).  Both columns are deleted.  A
balance assertion is now untouchable by a schedule operation, so the reset
deletes periods, rebuilds, and re-derives the postings -- there is no window
in which the schema is inconsistent and nothing to defer.

Bounded for safety: it refuses if the user has ANY settled transaction.

**It does NOT repopulate the rebuilt schedule; the ROUTE does, and that is
ruling R-R38** (plan step R7d-c-1).  The door wipes, rebuilds and re-syncs
both posting families, then returns EMPTY periods; the caller opens the
generate pass and fills them, which is why the populate now runs AFTER both
re-syncs rather than between the rebuild and them.  That order is safe because
every read either re-sync makes of ``budget.transactions`` or
``budget.transfers`` is keyed on a set of ids taken from the POSTED ledger, and
a freshly generated row is ``Projected`` and posts nothing.  A case here that
asserts recurring ROWS runs ``_reset_and_populate``; ``POST
/pay-periods/reset`` is graded in the
``tests/test_routes/test_pay_period_admin/`` package.

All four disciplines apply, and carry extra weight here because the
failure mode is silent balance corruption: the structural invariant
checker, which most cases run after the reset (Discipline 1), hand-computed
as-of balances with the anchor balance PRESERVED across the
wipe-and-rebuild (Discipline 2), the production integrity checker
(Discipline 3), and the adversarial set -- settled refusal with the DB
unchanged, a brand-new not-yet-anchored user, a rule's stated start and an
every-N rule's phase surviving the wipe with nothing re-pointed, and every
account's asserted balance surviving it with nothing re-anchored
(Discipline 4).  ``today`` is pinned with ``freeze_today`` so the anchor
resolution is deterministic.  The four disciplines are named in
``docs/plans/historical/implementation_plan_pay_period_crud.md``, a
historical record that governs nothing.
"""
