"""
Shekel Budget App -- Pay Period Writer Tests (plan steps C3-b and C4-c)

``pay_period_write`` is the one place in ``app/`` that changes
``budget.pay_periods``, so this is the one suite that grades what may be
written.  It came from ``test_pay_period_service.py`` when the writer left
that module; the classes carried over unchanged are the batch-shape and
refusal ones, and the classes below them are C3-b's own.

**What the writer DOES, and therefore what these tests are about:**

* **It writes one column.**  A row is one fact -- the payday -- since plan step
  ``pay_calendar:C4-c`` dropped ``end_date`` and ``period_index``.  Recording a
  payday INSERTs a row and touches no other; retiring one DELETEs it and
  touches no other (``TestAWriteTouchesNoRowItDidNotName``).  C3-b's own
  subject -- holding the two derived columns equal to
  ``pay_calendar.derive_periods`` on every write -- has no subject left, and
  the four cases that graded that machinery went with the columns.
* Every three-tuple asserted below is therefore the owner's DERIVED calendar
  (see :func:`_paydays`), which is what the writer's callers read.  The figures
  are the ones those cases were hand-computed against and they did not move;
  dropping the columns is only correct if they do not.
* The old batch guard ``_reject_overlapping_batch`` split into TWO rules, and
  only ONE survives.  The forward-only FLOOR stays, and exists only to keep plan
  step C6's mid-schedule insert closed.  The COVERAGE rule -- which refused a
  write that would take a filed row's money-day out of every paycheck -- was
  DELETED by developer ruling 2026-08-11; ``TestACoverageWithdrawalIsAccepted``
  is what grades the state it used to refuse.
* A batch that records at least one payday persists its cadence; one that
  records none leaves it alone (findings **P12**, **P29**).

Clock discipline (``.claude/rules/testing.md``): every date here is a literal
or derived from one by ``timedelta``, and nothing calls ``date.today()``, so
these pass identically under ``TZ=Pacific/Kiritimati``.
"""
