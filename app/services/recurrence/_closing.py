"""
Shekel Budget App -- Everything that stops a definition (plan step R7d-d)

A recurring definition stops for more than one reason, and only one of them is
written on the rule.  :mod:`app.services.recurrence._bounds` owns the bound the
OWNER AUTHORS -- indefinitely, on a date, after a count -- which the form
offers, the schema accepts and two columns store.  This module owns the other
kind: a stop the definition did not author, DERIVED from something outside the
rule, and the value that holds both.

Why the second kind exists at all
---------------------------------

A recurring transfer that pays a loan stops when the debt does, whatever its
rule says.  Until plan step R7d that fact was CACHED into the authored bound's
own column -- ten call sites wrote ``budget.recurrence_rules.end_date`` from the
loan's derived payoff between them -- so one column held two facts and every
reader was trusting that some earlier write had been recent enough.  That is
``CLAUDE.md`` rule 14's stored-and-derived case: the column is a stale cache
with no reconciler, and the remedy is to delete a home rather than keep two in
step.

Deleting it leaves the question this module answers: the two stops are
different KINDS, they are both real, and something has to hold them together.

**They are ANDed, never substituted.**  A definition fires only while BOTH
allow it, and each direction of getting that wrong costs money.  Ignore the
authored bound and the app models cash the owner has said will stop moving;
ignore the derived one and it projects payments against a debt that is gone.

The composition is a VALUE, not a step every reader performs
------------------------------------------------------------

:class:`Closing` holds both and answers for both, and
:attr:`~app.services.recurrence.ResolvedRecurrence.closing` is where a resolved
recurrence carries it.  So the occurrence walk narrows WITHOUT gaining a
parameter: whatever narrowing the value carries travels with it through any
call depth, and no caller re-threads it.

An optional ``narrowed_by=`` argument on the walk would have been a smaller
diff, and it would have left each of the five surfaces that ask "does this
still fire" -- generation, the Recurring surface's next date, its cadence
sentence, the ``/obligations`` and ``/savings`` totals, and the recurrence
form's preview -- one forgotten keyword from a plausible wrong answer that
nothing raises on.

**What that does NOT yet buy is unreachability, and an adversarial review of
this step corrected an earlier paragraph here for claiming it.**
:func:`~app.services.recurrence.resolve` builds a :class:`Closing` with no
derived half, because a spec names no destination and this package cannot fold
a balance -- so ``resolved_recurrence``, ``read_rule`` and ``rule_occurrences``
all still hand back a value nothing has narrowed.  The composed door is what
narrows; a caller that goes round it gets the rule's own answer, which is
correct for the definitions that have no derived stop and incomplete for the
ones that do.  The production readers took the door one R7d leaf at a time --
the Recurring surface at R7d-d, the monthly totals at R7d-e, the form's locked
control at R7d-f-1, GENERATION at R7d-c-2 (the reader whose answer becomes
rows) and the form's live preview at R7d-f-2
(``recurring_definition.resolved_submission``) -- and what still goes round it
in ``app/`` (census 2026-09-11 on the tree R7d-c-2 shipped on, re-read
2026-09-13 on R7d-f-2's) is two readers that ask no closing question -- the
write door's refusal and the opening-bound comparison in
``loan_recurrence_sync._sync_loan_cadence`` -- and ONE that goes round it on
purpose: the balance seam's definition walk (``balance_at._definition_walk``,
plan steps R16-b-2 and C9), which walks every definition paying into a loan under
its AUTHORED closing alone, because the derived stop is that fold's own output and
a fold reading its own answer would be a fixed point (ruling **R-R65**).  So
since R7d-f-2 "a reader that asks the closing question cannot reach the
un-narrowed answer" is a claim this module may make, and the seam's walk is
the one caller that must reach it.

**The derived stop's SHAPES are here and its RESOLVER is not.**  Deciding
which shape applies means folding a loan's balance, which needs the balance
seam -- and no module in this package imports ``balance_at``, nor may one:
:func:`app.services.loan_recurrence_sync.loan_payment_window` is the resolver
and it imports this package, so the arrow is one-way and the reverse would be a
cycle.  (The package is not ORM-free -- ``_authoring`` holds the write door and
flushes -- so the line that matters is the seam, not the ORM.)  The shapes are
plain values over dates, so they live where every consumer can see them: the
walk in :mod:`app.services.recurrence._occurrence`, and the phrase-writer in
:mod:`app.services.recurrence._describe`, which could not import the loan module
at all.  Rule 14 in its placement clause: where a layer puts the shared leaf out
of reach, MOVE THE LEAF.

**No IDENTIFIER here names a loan**, which is what keeps the vocabulary honest
even though the docstrings below use loans as their worked examples.  A loan is
one supplier of a derived stop, and a savings goal that is met or a card that is
closed could supply another.

Pure: no Flask, no ORM, no clock, no database.
"""
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import cache

from app.services.recurrence._bounds import (
    BoundReading,
    EndBound,
    date_bound_has_closed,
)


class DerivedStop(ABC):
    """A stop the definition did not author -- three shapes.

    What something OUTSIDE the rule allows, as one value with three shapes
    rather than the ``date``-or-``None`` pair the cached column collapsed it
    to.  That pair spells two of the three alike: a real closing date and a
    window that closed before the definition ever fired are both a ``date``,
    and telling them apart is what ``ck_recurrence_rules_valid_window`` was
    drafted for and then held back on (plan ledger row **D35**).  An owner
    AUTHORING a stop before a start has made a mistake to report; a loan trued
    to zero before its first installment has an empty window that is CORRECT at
    nought occurrences, and a CHECK that cannot tell those apart would have
    turned a true-up into an unhandled ``CheckViolation`` while the column
    held both.  The CHECK landed at plan step R7d-g, once the column held
    only what owners author and this value alone carried the derived stop.

    **Every shape answers :meth:`admits`, and this base implements none of
    it.**  A default here -- "a shape that does not recognise the question
    keeps firing" -- is the partial-function-over-a-closed-set defect this arc
    exists to remove, and on a loan it would go on charging a debt the owner
    has cleared.  ``@abstractmethod`` makes a half-written fourth shape
    unconstructible rather than merely wrong; :class:`EndBound` states the same
    contract one concept over, and for the same reason.

    **It is NOT an** :class:`EndBound`, deliberately.  That type is what the
    form OFFERS, the schema ACCEPTS and the two columns STORE, so each of its
    shapes owes a ``token``, a ``from_payload`` and a ``columns()``.  A derived
    stop is none of those things: nothing offers it, nothing posts it, and
    after plan step R7d-g nothing stores it.  Adding a shape there would put an
    unauthorable, unstorable member in the closed set the picker is derived
    from.

    **Every shape answers :meth:`has_closed` too, since plan step R7d-e**,
    which is the question ``obligations_aggregator`` asks to decide whether a
    commitment still belongs in the ``/obligations`` and ``/savings`` monthly
    totals.  Until that step the aggregator asked it of the AUTHORED bound
    alone (:func:`app.services.recurrence.has_ended` read the rule's own
    columns), so a retired loan's payment went on inflating both totals until
    some chokepoint happened to rewrite the cached column -- and left them the
    day it did, which is a fact about when a page was saved rather than about
    the loan.
    """

    @abstractmethod
    def admits(self, occurrence: date) -> bool:
        """Return whether the derived source still covers *occurrence*.

        **Every occurrence walk in this project is ASCENDING, so the first
        ``False`` is also the last one worth asking about**: a caller STOPS
        rather than skipping, and a shape answering ``False`` for one
        occurrence and ``True`` for a later one would be a stop that reopens.
        Stated here because it is a contract over all three shapes rather than a
        property of any one of them -- the same contract
        :meth:`~app.services.recurrence.EndBound.admits` states.

        Args:
            occurrence: The date the definition's cadence names.  The
                OCCURRENCE and never the pay period it is funded from (ruling
                **R-R6**): a period whose payday precedes a loan's payoff can
                contain an installment that follows it.

        Returns:
            ``True`` while the derived source still covers *occurrence*.
        """

    @abstractmethod
    def has_closed(
        self, *, on: date, reading: Callable[[], BoundReading],
    ) -> bool:
        """Return whether this stop had ended the definition by *on*.

        **ONE reading for a derived stop, and it is R-R45's** (ruling
        **R-R57**, developer 2026-09-05): the definition OWES no occurrence on
        or after *on*.  Not "the closing date has passed".  The two agree for a
        loan that runs to its last installment -- a payment due the 22nd of
        each month whose loan closes 2029-02-22 owes that day's installment and
        leaves the totals on 2029-02-23 under either reading -- and part where
        a loan is cleared MID-period: trued to zero on 2026-09-01 after its
        2026-08-22 installment, the definition owes nothing from 2026-08-23
        under this reading and would have counted a payment no occurrence
        backs for ten more days under the other.  The same reading the
        authored bound took at plan ledger row **D33**, so two ways of stopping
        one definition cannot disagree about the day it stopped.

        The signature is :meth:`~app.services.recurrence.EndBound.has_closed`'s
        unchanged, so :class:`Closing` asks both of its stops the one question
        over one reading.

        Args:
            on: The day being asked about, normally "today".
            reading: Called with no arguments for the definition's
                :class:`~app.services.recurrence.BoundReading` -- the
                occurrences the walk emitted under the WHOLE closing, so
                already narrowed by this stop, and the schedule's horizon.
                A shape that can answer without it never calls it.

        Returns:
            ``True`` when the definition owes no occurrence on or after *on*
            because of THIS stop.  A schedule that has not been extended far
            enough to say so answers ``False``, for the reason
            :func:`~app.services.recurrence._bounds.date_bound_has_closed`
            gives: that is the schedule's limit, not the definition's end.
        """


@dataclass(frozen=True)
class ClosesOn(DerivedStop):
    """The derived source names a date the definition stops on.

    For a loan payment: the loan's DERIVED closing date
    (:attr:`~app.services.balance_at.LoanFigures.closing_date`, plan step
    ``recurrence:R7d-h``) -- the date its balance folds to zero ahead while
    it still owes, or the day it LAST became closed once it does not.

    Attributes:
        on: The last day an occurrence may fall on, INCLUSIVE, for the same
            reason :class:`~app.services.recurrence.EndsOnDate` is inclusive:
            the balance reaches zero AT that installment, so the installment
            itself is owed and an exclusive bound would drop a loan's final
            payment from every projection.
    """

    on: date

    def admits(self, occurrence: date) -> bool:
        """Admit occurrences up to and including :attr:`on`.

        Args:
            occurrence: The date the cadence names.

        Returns:
            ``True`` when *occurrence* falls on or before :attr:`on`.
        """
        return occurrence <= self.on

    def has_closed(
        self, *, on: date, reading: Callable[[], BoundReading],
    ) -> bool:
        """Return whether the definition owes no occurrence on or after *on*.

        The date-bound closure rule
        (:func:`~app.services.recurrence._bounds.date_bound_has_closed`)
        applied to :attr:`on` -- the same rule the authored
        :class:`~app.services.recurrence.EndsOnDate` applies to its own day,
        because a last admitted day is the same bound whoever states it.

        Args:
            on: The day being asked about.
            reading: Called only while the closing date is still ahead.

        Returns:
            ``True`` when the closing date has passed, or when no occurrence
            falls in ``[on, self.on]`` and the schedule reaches far enough to
            say so.
        """
        return date_bound_has_closed(self.on, on=on, reading=reading)


@dataclass(frozen=True)
class Indefinite(DerivedStop):
    """The derived source stops nothing, so only the authored bound binds.

    For a loan: negative amortization, or an underpayment too severe to clear
    even the plan's post-contractual extension.  The payments must keep
    generating -- the loan still owes -- until the owner raises them, which is
    what plan step C7's payment-drift warning exists to prompt.  Answering
    anything else would silently stop projecting a debt the owner is still
    paying.
    """

    def admits(self, occurrence: date) -> bool:
        """Admit every occurrence.

        Args:
            occurrence: Unread -- a stop that names no date measures nothing.

        Returns:
            Always ``True``.
        """
        return True

    def has_closed(
        self, *, on: date, reading: Callable[[], BoundReading],
    ) -> bool:
        """Never close: a stop that names no date ends nothing.

        Args:
            on: Unread.
            reading: Never called.

        Returns:
            Always ``False``.
        """
        return False


@dataclass(frozen=True)
class Empty(DerivedStop):
    """The derived source closed BEFORE the definition's first occurrence.

    A loan originated 2026-06-20 with a ``payment_day`` of 15 owes its first
    installment 2026-07-15; true its balance to zero on 2026-06-21 and it
    retires that day, so the derived window is ``[2026-07-15, 2026-06-21]`` --
    CORRECT at nought occurrences.  Plan ledger row **D35** carries the same
    shape as the state that held ``ck_recurrence_rules_valid_window`` back
    until plan step R7d-g, because a CHECK could not tell it from an owner's
    mistake while both were stored; it is this VALUE now and never a column.

    **This shape is STABLE, and plan step ``recurrence:R7d-h`` is what made it
    so.**  A retired loan's closing bound USED TO BE the read pass's own
    ``as_of``, so this shape was TRANSIENT on the retired branch: the same
    untouched loan answered ``ClosesOn(today)`` from the day the as-of reached
    the first occurrence, and its admitted set grew one occurrence per cadence
    period.  The bound is now the day the loan LAST became closed
    (:attr:`~app.services.balance_at.LoanFigures.closing_date`), a fact about
    the LOAN rather than about when the page was rendered, so an untouched
    retired loan answers the same window on every read.

    **It admits exactly what a** :class:`ClosesOn` **before the same first
    occurrence admits -- nothing -- so it is a PRECOMPUTATION of a comparison
    its readers could make, held once where they would each make it.**
    Generation cannot tell the two apart and does not need to: it emits nothing
    either way.  What differs is what a reader may SAY -- a DISPLAY surface
    naming "until Jun 21, 2026" for a definition that fires from the 15th is
    false about a date, where "never runs" is true.
    """

    def admits(self, occurrence: date) -> bool:
        """Admit nothing.

        Args:
            occurrence: Unread -- an empty window covers no date at all.

        Returns:
            Always ``False``.
        """
        return False

    def has_closed(
        self, *, on: date, reading: Callable[[], BoundReading],
    ) -> bool:
        """Closed on every day: a window that admits nothing owes nothing.

        The loan cleared before its first installment owes no occurrence on or
        after ANY day, so the answer does not depend on *on* and needs no walk
        -- the precomputation this shape IS, reached from the other side.

        Args:
            on: Unread.
            reading: Never called.

        Returns:
            Always ``True``.
        """
        return True


#: The stop of a source that stops nothing.  A module-level singleton because
#: the shape carries no data, exactly as :data:`.._bounds.NEVER_ENDS` is one;
#: frozen dataclasses compare by value, so ``==`` answers for a fresh instance
#: too and no caller has to know which it holds.
INDEFINITE: Indefinite = Indefinite()

#: The stop of a source whose life closed before the definition's first
#: occurrence.  See :data:`INDEFINITE` for why it is a singleton.
EMPTY: Empty = Empty()


def stop_on_closing_date(
    closes: "date | None", first_occurrence: date,
) -> DerivedStop:
    """Return the derived stop a source's closing date states for one definition.

    **The ONE mapping from a closing date to a shape.**  A source that never
    closes (``None``) stops nothing (:data:`INDEFINITE`); one that closed
    before the definition's first occurrence admits nothing (:data:`EMPTY`);
    any other admits through its closing day (:class:`ClosesOn`).  Two readers
    ask it, and the second is why it is stated here rather than in either:
    ``loan_recurrence_sync.loan_payment_window``, the stop generation, the
    Recurring surface and the form read through the composed door, and the
    balance seam's estimate of a loan payment's paying side past the last
    saved paycheck (plan step ``pay_calendar:C9``, task plan#3941).  So the
    occurrences generation writes and the occurrences the estimate debits are
    admitted by one rule (``CLAUDE.md`` rule 14).

    Args:
        closes: The source's closing date (for a loan,
            :attr:`~app.services.balance_at.LoanFigures.closing_date`), or
            ``None`` when it never closes.
        first_occurrence: The definition's RESOLVED first occurrence
            (:attr:`~app.services.recurrence.ResolvedRecurrence.starts_on`),
            never a stored column (plan ledger row **D39**).

    Returns:
        The :class:`DerivedStop` the closing date states.
    """
    if closes is None:
        return INDEFINITE
    if closes < first_occurrence:
        return EMPTY
    return ClosesOn(on=closes)

#: The closed set of shapes a derived stop can take.
#:
#: **A weaker guarantee than :data:`.._bounds.END_BOUND_KINDS`, and an
#: adversarial review of this step corrected an earlier note here for claiming
#: parity.**  That tuple is ITERATED in application code -- ``_picker`` derives
#: the offered set from it -- so a shape absent from it is unofferable and
#: therefore unreachable.  Nothing derives construction from this one: a shape
#: absent here is still constructible.  What it buys is that the wording table
#: in :mod:`app.services.recurrence._describe` is GRADED against it, so a shape
#: added without a phrase fails a test rather than rendering a cell that omits
#: the stop.  Do not read it as making anything unreachable.
DERIVED_STOP_KINDS: tuple[type[DerivedStop], ...] = (
    Indefinite,
    ClosesOn,
    Empty,
)


@dataclass(frozen=True)
class Closing:
    """Everything that stops one definition: what was authored, and what is derived.

    The composed value a resolved recurrence carries
    (:attr:`~app.services.recurrence.ResolvedRecurrence.closing`), so that the
    walk, the phrase and every later consumer read ONE thing rather than each
    performing the same conjunction.

    **The conjunction is COMPUTED, never maintained**, which is the difference
    between this and the column it replaces.  Each stop is stated once, in one
    place, by whoever owns it; nothing has to be kept in step with anything,
    so there is no invariant here for a reconciler to enforce and none for a
    writer to forget.  ``CLAUDE.md`` rule 14: an invariant that cannot be
    violated because there is nothing to violate is worth more than one a
    reconciler enforces.

    Attributes:
        authored: The bound the OWNER stated -- :data:`.._bounds.NEVER_ENDS`
            for the many live rules that state none.  Always present: a
            definition always has an authored bound, even when that bound is
            "it does not stop".  **This value cannot tell an authored date
            from a cached one**, which mattered until plan step R7d-g: the
            ``end_date`` column of the loan payment the app bounded held the
            chokepoints' cache of the derived payoff, so the composed door
            supplied ``NEVER_ENDS`` here for that definition (ruling
            **R-R56**).  Nothing writes a cache there now, so this is the
            owner's word for every definition.
        derived: What something outside the rule allows, or ``None`` when
            nothing does.  ``None`` is "no derived source bounds this
            definition" and is a complete answer rather than an unknown -- a
            transaction template pays into no account at all, and a transfer
            into a savings account has no derived stop.  It is NOT a fifth
            :class:`DerivedStop` shape: the three shapes are the answers a
            source gives, and having no source is not one of them.
    """

    authored: EndBound
    derived: DerivedStop | None = None

    def admits(self, *, emitted: int, occurrence: date) -> bool:
        """Return whether an occurrence walk may still emit *occurrence*.

        BOTH must allow it.  The signature is
        :meth:`~app.services.recurrence.EndBound.admits`'s unchanged, so the
        walk that already asked the authored bound asks this instead and no
        call site learns a new shape.

        Args:
            emitted: How many occurrences the walk has already yielded -- what
                a count bound is measured against.  A derived stop never reads
                it: nothing outside the rule counts the rule's own firings.
            occurrence: The date being considered.

        Returns:
            ``True`` while every stop this holds is still open.
        """
        if not self.authored.admits(emitted=emitted, occurrence=occurrence):
            return False
        return self.derived is None or self.derived.admits(occurrence)

    def has_closed(
        self, *, on: date, reading: Callable[[], BoundReading],
    ) -> bool:
        """Return whether ANYTHING that stops this definition had done so by *on*.

        EITHER stop ending it ends it, so the two answers are ORed exactly as
        :meth:`admits` ANDs them: a definition fires only while both allow it,
        and it has ended once either does not.  Plan step R7d-e; the reading
        each stop takes is ruling **R-R45**'s, restated for the derived half by
        **R-R57**.

        **Both stops judge ONE reading, and asking twice costs one walk.**  The
        reading is the walk made under this whole value, so it is already
        narrowed by both stops; each shape then asks it against its OWN last
        day (:func:`~app.services.recurrence._bounds.date_bound_has_closed`),
        which is why no arbitration between the two is needed -- a stop the
        walk never reached cannot be the one that ended it, and a stop it did
        reach answers for itself.  The callable is memoised here rather than
        by the caller, so a caller cannot forget to and pay for the walk twice
        on a pair that asks for it twice: an authored date bound still ahead,
        or a count bound still unspent (that shape always reads the walk),
        beside a closing date still ahead.

        Args:
            on: The day being asked about, normally "today".
            reading: Called at most once, for the definition's
                :class:`~app.services.recurrence.BoundReading`.

        Returns:
            ``True`` when either stop says the definition owes no occurrence
            on or after *on*.
        """
        once = cache(reading)
        if self.authored.has_closed(on=on, reading=once):
            return True
        return self.derived is not None and self.derived.has_closed(
            on=on, reading=once,
        )


__all__ = [
    "DERIVED_STOP_KINDS",
    "EMPTY",
    "INDEFINITE",
    "ClosesOn",
    "Closing",
    "DerivedStop",
    "Empty",
    "Indefinite",
    "stop_on_closing_date",
]
