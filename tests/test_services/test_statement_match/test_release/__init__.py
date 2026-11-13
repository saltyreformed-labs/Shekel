"""The UNDO: what releasing a match takes back.

Plan step **bank_import:X-f6f**, ruling **R-GG**.  The create-a-purchase arm
had no inverse (findings **N-333** and **N-340**), and the state that left was
measured through these very doors before a line of the remedy was written:

* releasing a match that CREATED a purchase kept the purchase and the budget
  line it minted -- ``removed_count: 0`` in the release's own log line;
* no door in ``app/`` removed that purchase: ``entry_service.delete_entry``
  refused with *"Transaction 1 has settled; its purchases are closed"*, which
  is finding **N-333**, and all 103 purchases the developer's 2026-08-21 pass
  created sit under exactly that parent;
* recording the same `-$57.96` line again moved the balance **`-$115.92`** and
  left two purchases and two budget lines for one swipe.

Every test below is a FIRING CONTROL over one of those, written so it fails if
the remedy is removed.

**The arithmetic the root-cause half rests on was measured, not argued.**
Removing a POSTED purchase from a ``purchases``-basis close leaves that row's
own cash leg at ``0.00`` before and after and reverses only the purchase's own
leg (``822.04 -> 880.00`` on the account); removing an UNPOSTED one moves the
leg ``-57.96 -> 0.00``, which is the close re-pricing itself on a past day.
:class:`TestTheSettledParentRuleIsTheArithmetic` pins both.
"""
