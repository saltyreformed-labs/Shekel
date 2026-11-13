"""A bank line the account's books cannot hold is neither offered nor matched.

Plan step **balance:X-f3c-2b-2b**, finding **N-383**, ruling **balance:R-HG**.

An opening equity is the balance at the CLOSE of the day the books open, so
every dollar the bank moved on or before that day is inside the one figure.
Recording such a line -- as a purchase, as income, or as the evidence for
settling a row the owner already had -- counts that money twice.

**The two halves this module grades, and the second is a MONEY defect the
first would not have caught.**

* The review pass no longer OFFERS such a line: it reaches no card, no
  proposal and no create control, and what the screen says instead is a bound
  naming the opening and the restatement act.
* The three doors REFUSE it, which the offer split does not make redundant: a
  page rendered before a restatement moved the books forward posts line ids
  the current books no longer hold.  The match door is the sharp case --
  ``record_match`` settles every member on the LATEST of its bank days, so a
  GROUP holding one pre-opening line and one later line settles after the
  books open and clears ``reject_movement_before_books_open`` untouched.

**Measured on a restored production clone 2026-08-31, at this branch's
migration head, with the developer's own 378 SECU lines loaded**: Checking's
books open 2026-03-26 holding `$689.16`, which is the pay calendar's own first
day, so four lines post inside the opening.  Before this step two of them were
offered live controls with no withholding at all -- a `$2,573.42` payroll
deposit and a `-$108.87` Amazon swipe -- and the other two were PROPOSED
matches inside the one-click sweep.  Accepting a group of the 2026-03-26
`-$15.96` line with the 2026-08-17 `-$64.04` line against one `$80.00`
envelope was ACCEPTED and settled it on 2026-08-17.

**Why the fixture restates the books FORWARD.**  A factory account's books
open the day before the bootstrap pay period, so every line inside them is
also before the pay calendar and the calendar bound catches it first -- the
two bounds are disjoint and the calendar is applied first.  The shape that
exercises this one is the developer's own: books opening ON the calendar's
first day.  :func:`_helpers._books_open_on` goes through the table's ONE
writer rather than a raw ``INSERT``, which is the right habit -- but it is NOT
evidence that a door could produce the state, and a first version of this
paragraph said it was.  ``stage_account_opening`` explicitly does not bound
the day; the DOOR does.  What makes these fixtures production shapes is that
the day chosen is one ``apply_opening_restatement`` would accept, which is
asserted where it matters -- the door's own day cases,
``TestTheDaysItRefuses`` in ``test_opening_restatement/test_the_writer.py``.
"""
