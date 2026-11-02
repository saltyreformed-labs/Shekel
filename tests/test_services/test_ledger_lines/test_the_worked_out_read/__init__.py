"""Tests for the worked-out read (plan task plan#2526).

:func:`app.services.ledger_lines.worked_out_lines` answers, for one owner and
one scenario, every line of the ledger worked out from the records by the
rules the stored ledger's writers post from: each posting movement's two lines
in its parent's paycheck on its own bank day, and each account's and loan's
correction lines filed by their day.

What these cases pin:

* **it issues no write of its own and reads no stored ledger**: on a clean
  session no statement it issues writes, or names the stored journal or
  chart, and nothing is left pending; on a dirty one its one write is the
  caller's pending change, autoflushed, and its lines read that change;
* **whose records it reads**: an owner with none reads nothing and asks no
  pay calendar, and another owner's movements are never read, even in that
  owner's scenario;
* **the lines are the rules' own**, written out by hand: a bill, a transfer,
  an account's opening and true-up, a loan's anchors and payment split;
* **a source that LEFT the settled set has no line** (finding N-162,
  plan#1356): after a revert, a revert then Credit, a revert then Cancel, a
  delete, and a transfer's soft delete, the read is what it was before the
  source settled -- the corrections a later balance check books included;
* **a transfer's effect has one rule** (finding N-164, plan#1358): each side
  is its own movement's two lines, valued by the producer the cash fold
  values it by;
* **a refund, a card purchase and an over-posted envelope** (finding N-318,
  plan#1304): money back books as an inflow against the category, a card
  purchase books nothing, and a purchase past its envelope's figure books its
  whole figure OUT, never in;
* **its statements do not grow with the number of sources** (finding
  plan#2147's requirement, ruling plan#2136), **nor with the number of
  accounts or loans** (ruling plan#2623).

Whether the lines equal the STORED ledger's to the cent is the parity test's
(plan#2527) and the production run's (plan#2528).
"""
