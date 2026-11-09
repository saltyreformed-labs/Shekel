"""Tests for an account's anchor corrections: the walk, and the lines it books.

A NON-loan account's anchor history books on the worked-out ledger
(:func:`app.services.ledger_lines.worked_out_lines`): the once-per-account
OPENING (its stored opening equity, ``budget.account_openings``) and a TRUE-UP
per assertion, each a balanced correction landing the account's own line on the
asserted balance as of the day it is the closing balance for.  The walk
(:func:`app.services.ledger_lines.walk_account_ledger`) partitions source facts
by their settled CIVIL DAY (the stored ``settled_on``; a transfer by its side's
covering movement on the walked account) against each anchor's stored
``observed_on`` -- never by pay period (the plan review's CRITICAL-1) and never
by instant, which decided the question by click order and cost production
$4,001.42 (ruling R-DH).

These were the store's writer's unit tests (``account_posting_service``) until
plan step plan#2914 deleted the writer: each case whose figures were also read
off the worked-out ledger kept them, and lost the writer's call and the stored
entries it read; each whose subject was the writer itself (an entry's count, a
repeat sync, the per-user resync, the writer's reversal of what it had posted,
the writers' shared filing door) went with it.

Fixtures are SYNTHETIC with HAND-COMPUTED literals, each docstring showing the
arithmetic.  Events are placed RELATIVE to the origination assertion's own
``observed_on`` (:func:`._helpers._origin_day`) in whole DAYS, through
``settle_instant_on`` so each lands at noon UTC on the intended civil day; a
case needing two events on ONE day uses a PINNED opening instead.  Both
conventions exist because a fixture that offsets from a RECORDING instant grades
a different case than it names -- twice now, from two different causes
(finding N-133 / F2, and the review of the F1 revert).  All money is ``Decimal``
from strings.
"""
