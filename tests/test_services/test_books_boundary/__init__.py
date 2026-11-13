"""A movement cannot predate the books it is in (plan step X-f3c-2b, **R-HG**).

**The rule, in one sentence.** An account's opening equity is the balance at the
CLOSE of ``budget.account_openings.opened_on`` -- the same rule
``account_anchor_history.observed_on`` states for an assertion (ruling R-DH (a))
-- so no cash movement may be dated ON OR BEFORE that day.

**Three tiers grade it here, and each covers what the one below cannot.**

* The PREDICATE (:func:`app.services.cash_ledger.reject_movement_before_books_open`),
  where the boundary itself is pinned from both sides so a ``<`` written where
  the code has ``<=`` fails.
* The ORM chokepoint (:func:`app.services.settle_day.record_settle_day`) and the
  doors above it, which is what makes the refusal a sentence a date box can
  render rather than a database exception at COMMIT.
* The DATABASE, which is the only tier that sees the writers the other two
  cannot: a bulk ``query.update()`` with no ORM instance, an opening restated
  FORWARD past a movement that already exists, and anything a future writer
  does that nobody enumerated.

**Why the third tier is graded here and not taken on trust.** The suite's own
history is the argument: ``reconcile_service.record_settled_days`` writes
``settled_on`` by bulk ``UPDATE`` precisely because it has no instance to hand
``record_settle_day``, and the ``@validates`` guard one column over documents
its own blindness to exactly that shape. A guard whose scope nobody measured is
a guard whose scope is wrong.
"""
