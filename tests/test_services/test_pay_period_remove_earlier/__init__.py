"""Tests for "Remove earlier paychecks" (plan step ``pay_calendar:C21``).

The undo of "Add earlier paychecks" (ruling **R-PC108**).  Ruling **R-PC111**:
the owner names the paycheck to START FROM, by id, and every paycheck before
it goes.  Ruling **R-PC109**: a paycheck may go only when it holds no money --
an unpaid row its template made and nobody changed goes with it, anything else
the owner entered, changed or paid refuses the removal and is named, as does
any entry the posted ledger booked in it, a pay stub on its payday, and money
DATED inside it anywhere in the budget.  Ruling **R-PC114** (amending
R-PC109's booked-entry clause): what the posted ledger booked in a removed
paycheck was re-filed through Reset's two re-syncs, until plan task plan#3076
deleted them; the worked-out ledger files it by its day.  Ruling **plan#2517**
(amending R-PC114): the removal is no longer refused when a booked total
moves, because worked out from the records no total can.  Ruling **R-PC110**:
at least one paycheck of the earliest pay rhythm always stays, and that
rhythm's start moves UP to the new first paycheck (the reverse of R-PC105's
move down).

Nine contracts, one class each:

* the PRODUCER (``pay_calendar.opening_rephase``) -- pure: the exact inverse
  of ``earlier_paydays``' re-phase over every cadence kind and convention,
  and ``None`` where a later era pays the opening;
* the DOOR (``pay_period_admin.remove_earlier_pay_periods``) -- what it
  removes, where it moves the phase, and that the paycheck it starts from
  stays;
* an ADD UNDONE -- add N with C18-b's door, remove them with this one,
  template rows and a recurring transfer included: paydays, eras, rows and
  every worked-out total come back (the test names what it compares);
* WHAT A PAYCHECK MAY HOLD (R-PC109's filed half), one case per kind;
* an OPENING RE-FILED (R-PC114) -- an account's and a loan's opening filed
  in an added paycheck go back (the store's cases beside them went at plan
  step plan#2914);
* NO WORKED-OUT TOTAL MOVES (plan#2517) -- openings and a loan's planned
  payments in the head, every total per chart key equal before and after;
* MONEY DATED inside the removed span (R-PC109's dated half), one case per
  arm, and the case the span excludes;
* the EARLIEST RHYTHM KEEPS A PAYCHECK (R-PC110);
* the refusals that name no paycheck -- an id that is not the owner's -- and
  a stated history, which a removal cannot put above the record.

``POST /pay-periods/remove-earlier`` is graded in
``tests/test_routes/test_pay_periods_remove_earlier.py``.
"""
