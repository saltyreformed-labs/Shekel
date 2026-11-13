"""Applying a whole reviewed pass in one request -- plan step X-f6a-3c-2.

Finding **N-306**.  The review screen offers two acts and, until this step,
each was its own request through its own money door: 215 round trips on the
developer's own statement, each paying 3.593 s of ``candidates_for`` before it
wrote a row.  This module is about what changed and what must not have.

**The three properties that carry the money risk:**

1. **Isolation.**  A refused item leaves NOTHING behind and the rest still
   land.  On the developer's own statement (2026-08-19) a few proposals were
   refused -- a settled credit-card payback whose derived figure had drifted
   -- so a pass that failed whole would have lost every good correction to
   one divergence.
2. **Freshness.**  One derivation serves the whole pass, so an item must not be
   handed a row or an envelope an EARLIER item in the same pass has claimed.
   That is what :func:`~app.services.statement_match.matched_subjects` being
   re-read per act buys, and it is the arm a snapshot would silently break.
3. **Failing loud.**  A broken invariant (a ``RuntimeError``) fails the request, not one item.

**What the collision cases here fix in place is a REAL case**: on the
developer's own statement some envelopes are both named by a proposal and
offered as a destination, so some of the creatable lines aim at one.
"""
