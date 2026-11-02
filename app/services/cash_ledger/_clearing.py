"""The ONE rule for WHICH statement cleared a line -- ruling R-FL.

*Is this movement already reflected in the balance the user declared?* is the
question the whole balance arc turns on, and until plan step X-f3a it was
answered by comparing two of the app's own dates:
``settled_on <= the latest assertion's observed_on``
(:meth:`~._amounts.ReconciledThrough.covers`).  The developer's own bank exports
measured that guess -- of 55 Checking assertions only 17 equal the bank's
closing balance for their day, and of 110 movements matched to bank lines on
exact amount only 33 carry the day the bank posted them -- and
``ReconciledThrough``'s own docstring had already named the remedy: *"what
removes it is an OBSERVATION, not a second derived date"*.

**The observation is a LINK.**  ``transactions.reconciled_by_id`` and
``transaction_entries.reconciled_by_id`` name the ``account_anchor_history`` row
whose statement showed the line, under a composite foreign key that makes a link
to another account's statement unrepresentable (migration ``d5b8e2c74a19``).

**Three states, not two** (developer ruling, 2026-08-14).  A line is

* **CLEARED** -- it names a statement;
* **UNKNOWN** -- it names none, and no statement has been recorded as walked
  line by line over its date, so the app does not know.  The date rule answers
  here, exactly as it always has;
* **NOT CLEARED** -- a statement covering its date WAS walked line by line and
  did not show it, so it is genuinely outstanding.

**The third state does not exist yet, and its absence is what makes plan step
X-f3a-1 balance-neutral.**  Recording that a statement was walked is X-f3a-2's
fact.  Until it exists every unlinked line is UNKNOWN, the date rule answers it,
and this module reproduces ``covers``' answer for every row in the database --
which is why nothing was backfilled: writing the guess into the link column
would launder it into an observation nobody made.

**What a CLEARED line's record may DECIDE is bounded, and the bound is a
theorem** (:meth:`StatementCoverage._recorded_anchor_id`).  While an assertion
RESETS the ledger, the fold can only render a line under a statement closing on
the day the date rule picks -- so the record decides between statements SHARING
a day, which is exactly what the reconcile panel writes, and elsewhere it is
kept on the row while the date rule answers.  Measured: honouring one
disagreeing link made a production clone render ``$2,246.58`` on a day its owner
had asserted ``$2,746.58``.  **Plan step X-f3c is what frees it**, because an
assertion stops resetting anything there.

**The date rule is therefore not a fallback bolted on** -- it is the answer for
UNKNOWN and the answer wherever the reset denies the record its say, and it
lives HERE rather than at any call site.  Ruling R-FL's own amendment required
that much: a link cannot simply REPLACE it, because
:func:`~._walk.dated_deltas` emits every source at its own day and only the
assertion's correction cancels the ones it absorbed.  Plan step X-f4 deletes it
when the cutover makes ``balance(T)`` a sum of postings.

**Assignment, not a prefix scan.**  Both balance walks advanced a monotonic
pointer through day-sorted sources, which is correct only while "cleared by this
assertion" is monotone in the day.  It is today -- the bound above is what makes
it so -- and it stopped being something the money may REST on: the walks now ask
per LINE and GROUP by the answer, so the loaders' ordering is a reproducibility
contract rather than a precondition of the balance, and X-f3c frees the record
without touching either walk again.

**This module imports NOTHING from its own package**, and that is deliberate
rather than incidental: the rule is what every other module in the cash ledger
asks, so a dependency in this direction would make the import graph a cycle the
moment the reservation (:mod:`._amounts`) needed it -- which it does.  What the
rule needs from a statement and from a line is stated as the two protocols
below, so the contract is readable without opening ``_events``.

Services-boundary discipline: plain data in, plain data out; no Flask import, no
query, no write.  :func:`~._events.coverage_for` is the one convenience that
READS, and it lives beside the loader it wraps.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol


class AssertedStatement(Protocol):
    """What :func:`statement_coverage` needs to know about one assertion.

    Its identity and its day, which is all the rule reads:
    :class:`~._events.CashAnchorFact` satisfies it, and stating it here is what
    lets this module import nothing from its own package.

    Attributes:
        anchor_id: The ``budget.account_anchor_history`` row's own id -- the
            value a cleared line names.
        observed_on: The civil day the asserted balance was true for.
    """

    anchor_id: int
    observed_on: date


class ClearableLine(Protocol):
    """What :class:`StatementCoverage` needs to know about one line.

    The two facts, and nothing else: when the money moved, and which statement
    was recorded as showing it.  Three types satisfy it as they stand --
    :class:`~app.models.transaction.Transaction`,
    :class:`~app.models.transaction_entry.TransactionEntry` and
    :class:`~._events.CashSourceFact`, where the posted walk's own source record
    was a fourth until plan task plan#2525 deleted that walk's loaders --
    so the rule is asked of the row itself rather than of two loose arguments a
    caller could swap or forget (plan Section 8: an argument a caller can get
    wrong is a defect, not a contract).

    Attributes:
        settled_on: The civil day the money moved, or ``None`` when it has not
            been observed to move at all (a purchase nobody has seen post).
        reconciled_by_id: The ``account_anchor_history`` row whose statement
            showed this line, or ``None`` when none has been recorded.
    """

    settled_on: "date | None"
    reconciled_by_id: "int | None"


@dataclass(frozen=True)
class StatementCoverage:
    """An account's assertions, and which of them cleared a given line.

    Built once per account from its assertion facts
    (:func:`statement_coverage`) and asked per line.  It is the ONE
    implementation of ruling R-FL's rule, for the cash walk (one walk since
    plan task plan#2525, the posted ledger's corrections framing the read
    fold's replay), the entry reservation and the entry list's indicator --
    the same "one implementation rather than a convention" property
    :class:`~._amounts.ReconciledThrough` was built for, over the fact that
    replaced its guess.

    **It answers WHICH assertion, not merely WHETHER**, because the walk needs
    the first: an assertion's correction is ``asserted balance - the sum of what
    it cleared``, so a line assigned to the wrong assertion moves two
    corrections even when the day-level balance survives.
    :meth:`is_cleared` is the whether, derived from the which, so the two can
    never come to disagree.

    Attributes:
        anchor_ids: The account's assertion ids in walk order -- the order
            :func:`~._events.cash_anchor_facts` loads,
            ``(observed_on, created_at, id)`` ascending.
        observed_days: Each assertion's civil day, parallel to
            :attr:`anchor_ids` and therefore ASCENDING, which is what lets the
            UNKNOWN arm bisect instead of scan.
    """

    anchor_ids: tuple[int, ...]
    observed_days: tuple[date, ...]

    def clearing_anchor_id(self, line: ClearableLine) -> "int | None":
        """Return the id of the assertion that cleared *line*, or ``None``.

        Ruling **R-FL**, whole:

        * a line naming a statement is cleared by it where the RESET admits
          that -- see :meth:`_recorded_anchor_id`, which bounds the record to
          statements sharing the date rule's day and answers the date rule
          elsewhere;
        * a line naming none is UNKNOWN, and the date rule answers: the
          EARLIEST assertion whose day is on or after the line's own, because an
          assertion is the closing balance for its civil day (ruling R-DH (a))
          and the first one to close over a movement is the one that would have
          shown it.

        **That default reproduces the monotonic-pointer scan it replaces,
        exactly.**  Both walks absorbed sources in day order while the current
        assertion ``covers`` them, so a source landed at the first assertion
        dated on or after it -- which is what :meth:`_first_covering` returns.
        The difference is that this answer does not depend on any list being
        sorted the way a docstring says it is.

        A line the account has no assertion for at all -- dated after every one,
        or never observed to have moved -- is cleared by nothing and rides on
        top of the ledger, which is the honest answer and the one the app
        already gives.

        Args:
            line: Any :class:`ClearableLine` -- a transaction, a purchase, or a
                walk's source fact.

        Returns:
            The clearing assertion's ``account_anchor_history`` id, or ``None``
            when no assertion cleared it.

        Raises:
            RuntimeError: When *line* names a statement this account has not
                made -- a coverage built for the wrong account, which the schema
                makes otherwise unwritable (:meth:`_recorded_anchor_id`).
        """
        if line.reconciled_by_id is not None:
            return self._recorded_anchor_id(line)
        return self._first_covering(line.settled_on)

    def _first_covering(self, settled_on: "date | None") -> "int | None":
        """Return the earliest assertion closing on or after *settled_on*.

        The DATE rule -- ruling R-DH (a) -- as a bisect over the ascending
        ``observed_days``.

        Args:
            settled_on: The day the money moved, or ``None`` when nothing has
                been observed to move.

        Returns:
            The assertion's id, or ``None`` when no assertion closes over it.
        """
        if settled_on is None:
            return None
        index = bisect_left(self.observed_days, settled_on)
        if index == len(self.anchor_ids):
            return None
        return self.anchor_ids[index]

    def _recorded_anchor_id(self, line: ClearableLine) -> "int | None":
        """Return *line*'s recorded clearing assertion where the RESET admits it.

        **While an assertion is a RESET, the fold cannot render a link that
        disagrees with the date about the DAY**, and that is a theorem rather
        than a policy.  A balance at date D is the prefix sum of
        :func:`~._walk.dated_deltas`, which emits each source at its OWN
        ``settled_on`` and each correction at its assertion's day.  Writing
        ``P(d)`` for that prefix and ``A_j`` for the assertions in day order,
        the corrections telescope, so

            ``P(A_j.day) = A_j.asserted``
              + (sources dated on or before A_j.day)
              - (sources cleared by A_1..A_j)

        and the reset's whole promise -- ruling **R-S**, "an assertion always
        wins" -- is that those two sets are equal for EVERY j.  Under the date
        rule they are, by construction.  A link that moved a source across an
        assertion boundary would break the equality at every j between the two
        days, by the source's whole delta.

        **Measured on a production clone rather than argued**: honouring one
        such link made the fold render ``$2,246.58`` on 2026-03-27 for an
        account whose owner had asserted ``$2,746.58`` that day.  The mirror
        direction reads ``anchor + X``, which is the ``$4,001.42`` class the day
        partition exists to make unspellable.

        So the record is honoured where it only chooses between assertions
        SHARING a day -- which is what the reconcile panel writes, because it
        names the GOVERNING assertion while the date rule takes the first of
        that day, and production carries three such days on Checking.  That
        choice is real: it decides which correction the posted ledger books the
        line under.  It is invisible to every BALANCE, because the fold reads a
        day's boundary after every step on it.

        **Anywhere else the DATE rule answers, and it does not raise.**  A
        refusal was written first and an adversarial review's own question
        killed it: assertions are not immutable in effect, because a user may
        record a BACK-DATED one (``anchor_service.resolve_observation_day``
        bounds it only at ``earliest_recordable_day``).  Inserting one between a
        line's settle day and its statement's re-points the date rule and
        strands a link that was consistent when written -- so a raise here would
        be a 500 on every screen showing that account, reached by an ordinary
        act, with no in-app repair.  Falling back is not silence about a wrong
        answer: BOTH branches render the balance the user asserted, the record
        stays on the row for the reader that wants it, and what the reset denies
        it is only the power to move money.

        **The CUTOVER is what lifts the restriction**, not a wider rule here.
        Plan step X-f3c makes ``balance(T)`` opening equity plus the sum of
        postings; an assertion stops resetting anything, and a line may then
        clear on whatever day the bank says.

        Args:
            line: A :class:`ClearableLine` whose ``reconciled_by_id`` is set.

        Returns:
            The recorded assertion id where the reset admits it, else the date
            rule's own answer (possibly ``None``).

        Raises:
            RuntimeError: When the named assertion is not one this account made.
                Unwritable through the schema -- the composite foreign keys scope
                a link to the row's own account -- so it means this coverage was
                built for a DIFFERENT account than the line it was asked about,
                which is a programming error and not a data era.
        """
        covering = self._first_covering(line.settled_on)
        try:
            position = self.anchor_ids.index(line.reconciled_by_id)
        except ValueError:
            raise RuntimeError(
                f"clearing link {line.reconciled_by_id} names an assertion "
                f"this account has not made.  The composite foreign keys "
                f"fk_transactions_reconciled_by / "
                f"fk_transaction_entries_reconciled_by make a cross-account "
                f"link unwritable, so this is a coverage built for a DIFFERENT "
                f"account than the line it was asked about."
            ) from None
        if covering is None:
            return None
        if self.observed_days[position] != self.observed_days[
            self.anchor_ids.index(covering)
        ]:
            return covering
        return line.reconciled_by_id

    @property
    def latest_statement_day(self) -> "date | None":
        """Return the civil day of the account's most recent assertion.

        **For RENDERING and SQL bounds, never for deciding what cleared.**  It
        is the deliberate escape hatch
        :attr:`~._amounts.ReconciledThrough.observed_day` already carries, on
        the type that replaced it, and naming it that way is the point: reaching
        for a raw day is visible in review where a ``<=`` was not.  Its one
        caller is the entry list's caption ("already inside your balance as of
        Aug 6"), which does not ask whether a line is inside a balance --
        :meth:`is_cleared` is that question's only implementation.  (The
        reconcile panel bounds its offer set with the governing assertion's own
        ``observed_on``, threaded from ``cash_ledger.governing_anchor``, not
        with this.)

        The caption stays TRUE once links exist even for a purchase an EARLIER
        statement cleared: a line inside the Aug 2 balance is inside the Aug 6
        one too.

        Returns:
            The latest assertion's day, or ``None`` for an account whose owner
            has never declared a balance.
        """
        return self.observed_days[-1] if self.observed_days else None

    def is_cleared(self, line: ClearableLine) -> bool:
        """Return whether *line* is inside some balance the user declared.

        The question the entry reservation and the entry list's indicator ask --
        they need no assertion's identity, only whether the money is already
        counted -- and it is DERIVED from
        :meth:`clearing_anchor_id` rather than stated a second way, so a change
        to the rule cannot leave the reservation answering the old one.  That
        was the failure mode ``ReconciledThrough`` itself was built to prevent,
        one fact later.

        Args:
            line: Any :class:`ClearableLine`.

        Returns:
            True when some assertion cleared it.
        """
        return self.clearing_anchor_id(line) is not None


def statement_coverage(
    anchors: "Iterable[AssertedStatement]",
) -> StatementCoverage:
    """Return the clearing rule for an account, from its assertion facts.

    Takes the facts rather than an account id, so a caller already holding them
    -- a :class:`~._walk.CashLedgerWalk`, whose ``coverage`` this is -- pays no
    second query, and a caller that
    does not (:func:`~._events.coverage_for`) has exactly one place that turns
    an id into them.

    Args:
        anchors: The account's assertions in walk order
            (:func:`~._events.cash_anchor_facts`, ``(observed_on, created_at,
            id)`` ascending).  **The day order is this function's precondition
            and its only one**: :meth:`StatementCoverage.clearing_anchor_id`
            bisects ``observed_days``, so a list not ascending in
            ``observed_on`` would answer the UNKNOWN arm wrongly.  It is stated
            once at the loader (finding N-133 / R1) rather than re-sorted here.
            An empty list is legal and yields a coverage that clears nothing,
            which is the honest answer for an account whose owner has never
            declared a balance -- production-unreachable (migration
            ``cfb15e782f86`` plus ``account_service.create_account`` guarantee
            an opening row) and answered rather than raised, exactly as the walk
            answers it.

    Returns:
        The account's :class:`StatementCoverage`.
    """
    ordered = list(anchors)
    return StatementCoverage(
        anchor_ids=tuple(anchor.anchor_id for anchor in ordered),
        observed_days=tuple(anchor.observed_on for anchor in ordered),
    )
