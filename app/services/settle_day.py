"""
Shekel Budget App -- The settle DAY and how it is known

WHEN a row's money moved and HOW that day is known, as ONE value (plan step
**X-az**, finding **N-332**).  ``settled_on`` alone carried three different
kinds of fact -- a day the bank showed, a day a balance was asserted for, and a
day the owner typed -- and nothing on the row said which, so the statement
matcher told them apart by testing whether ``reconciled_by_id`` was populated.

**It is a VALUE TYPE rather than a second parameter, and that placement is the
whole structural half of this step.**  :class:`~app.services.status_seam.Settlement`
is its twin one column over: the figure and its basis are one fact, so they
travel as one value and a door cannot build a malformed pair to hand over.  A
loose ``settled_day_basis`` parameter beside ``settled_on`` would be two facts
with a default between them -- and a default is exactly how a caller silently
records the wrong provenance.  With one value there is no default to get wrong:
a door that knows a day states how it knows it, or it passes nothing at all.

**Why a shared leaf rather than a home in either door's package.**  Both
``budget.transactions`` and ``budget.transaction_entries`` carry the pair, and
their write doors are in different packages -- ``status_seam`` owns the
transaction column and ``entry_service`` owns the purchase one.  Neither owns
both, so a value type living in either would make the other import a package it
has no other business with.

**One QUERY, and it is the boundary rule** (plan step X-f3c-2b, finding
**N-378**).  :func:`record_settle_day` asks
:func:`app.services.cash_ledger.reject_movement_before_books_open` whether the
day it is about to write predates the account's opening equity -- money already
inside that figure, which the fold would then count twice.  It is asked HERE
because this function is the ONE assignment of ``settled_on`` on either table:
``status_seam.apply_status_change``, both ``entry_service`` doors and the
statement matcher all reach the column through it, so one call covers every ORM
path instead of three doors each remembering.  The one writer that does NOT
come through here is ``reconcile_service.record_settled_days``, a bulk
``query.update()`` with no ORM instance to hand over; it asks the same function
itself, and the database's own constraint trigger is what makes the rule hold
for a writer nobody enumerated.

Otherwise pure: a frozen value, one ``ref_cache`` lookup, and two functions that
read or write a row's two columns.  No session of its own, no commit, no Flask.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from app import ref_cache
from app.enums import SettledDayBasisEnum
from app.models.mixins import SettleDatedMixin, reject_settle_instant
from app.services.cash_ledger import reject_movement_before_books_open


@dataclass(frozen=True)
class SettleDay:
    """WHEN a row's money moved and HOW that day is known.

    Attributes:
        day: The civil day the money moved.  Never ``None`` -- a row with no
            day carries no :class:`SettleDay` at all, which is what the
            ``| None`` on every parameter typed with it means.
        basis: How that day is known
            (:class:`app.enums.SettledDayBasisEnum`).

    **The ``datetime`` refusal is in the constructor**, which is one layer
    earlier than it used to be reachable.  ``datetime`` subclasses ``date``, so
    the annotation above catches nothing and PostgreSQL truncates the instant on
    the UTC session clock -- filing an 8pm-Eastern settle under tomorrow
    (finding **N-179**).  The rule itself is
    :func:`app.models.mixins.reject_settle_instant`, stated once on the
    column and reused here rather than restated: refusing at construction means
    a wrong-typed day cannot even be packaged for a door, while the ORM
    validator stays the backstop for a direct column assignment.
    """

    day: date
    basis: SettledDayBasisEnum

    def __post_init__(self) -> None:
        """Refuse a value that is not a civil day.

        **Two refusals, and both are the class's own documented invariant made
        a predicate** -- a value type that states a rule and does not enforce it
        is the shape this project deletes.

        Raises:
            TypeError: When :attr:`day` is a ``datetime`` (from
                :func:`app.models.mixins.reject_settle_instant`).  A
                programming error at the call site -- no form can submit an
                instant -- so it is not a ``ValidationError``.
            ValueError: When :attr:`day` is ``None``.  A row with no settle day
                carries no :class:`SettleDay` at all -- that is what the
                ``| None`` on every parameter typed with this means -- so a
                ``None`` here is a caller that meant to pass no value and wrapped
                one instead.  Left unrefused it constructs a pair with a NULL day
                and a non-NULL basis, which every ``ck_*_settle_day_basis_pairing``
                makes unstorable, and which the refusals raise on first.
        """
        if self.day is None:
            raise ValueError(
                "A SettleDay states a day that HAPPENED, so it cannot wrap "
                "None. A row with no settle day carries no SettleDay at all: "
                "pass None in place of the whole value, which is what every "
                "door typed 'SettleDay | None' means by it."
            )
        reject_settle_instant(self.day)

    @property
    def basis_id(self) -> int:
        """Return the ``ref.settled_day_bases`` id for :attr:`basis`.

        Returns:
            int -- resolved through ``ref_cache.settled_day_basis_id``, which
            is how every ref member reaches SQL in this project.

        Raises:
            RuntimeError: If the ref cache has not been initialized.
        """
        return ref_cache.settled_day_basis_id(self.basis)


def is_evidence(day: SettleDay) -> bool:
    """Return whether *day* is its row's OWN, rather than borrowed.

    ``observed``, ``asserted`` and ``entered`` are evidence -- the bank, a
    balance assertion, or the owner said so -- and ``borrowed`` is the one
    member that is not (:class:`~app.enums.SettledDayBasisEnum`; only a
    transfer side is ever borrowed).  The ONE statement of that partition in
    ``app/`` (the REC-552 study's seam S1): a transfer side's day function
    (``transfer_service._side_days``), the popover's day boxes and the status
    seam's mirror all ask it.  It lives beside :class:`SettleDay` rather than
    in ``transfer_service`` because the seam, which ``transfer_service``
    calls, asks it too.  The relabel migration's frozen SQL states it for
    itself, because a migration may not import the application.

    Args:
        day: A stored or stated day.

    Returns:
        ``True`` unless *day* is borrowed.
    """
    return day.basis is not SettledDayBasisEnum.BORROWED


def submitted_settle_day(
    submitted_day: date, recorded: "SettleDay | None",
) -> "SettleDay":
    """Return the pair a FORM submission means -- and refuse to launder a basis.

    **The ECHO rule for the settle DAY, stated once for all three form doors**
    (the transaction PATCH, the transfer PATCH and the entry PATCH), and it is
    :func:`app.services.status_seam.figure_for_status`' own rule one column
    over: both full-edit forms and the purchase popover submit the row's WHOLE
    state on Save, so a day that arrives EQUAL to the day the row already
    records is the control coming back untouched.  It asserts nothing.

    **Without it the step re-opens the defect it closes, and the cost is
    measured.**  A submitted day was wrapped as ``entered`` unconditionally, so
    an untouched Save on a purchase the reconcile panel had ticked rewrote its
    ``asserted`` bound as the owner's own typing -- with the day unchanged, so
    nothing released the clearing link and nothing signalled the change.
    ``CandidateRow.expected_window`` then collapsed that purchase from the span
    ``(purchased_on, settled_on)`` to a POINT at the assertion day, putting it
    out of reach of its own bank line, which the merchant policy offers to
    RECORD: the ``$3,590.00`` / 50-duplicate mechanism finding **N-332** is made
    of, arriving through a different door.  Measured on production 2026-08-22:
    **59 of 66 linked purchases, ``$4,173.07``**, one innocuous save each.
    Found by two independent adversarial reviews.

    **It is not a confidence ranking.**  The members are separated by
    PROVENANCE and not by strength (:class:`app.enums.SettledDayBasisEnum`), so
    "a stronger basis wins" would be a rule the enum does not support.  What
    this says is narrower and true: a day that did not move carries no new
    evidence, so whatever evidence the row already recorded still stands.  A day
    that DID move is a fresh assertion by the person who typed it, and
    ``entered`` is exactly what that is.

    **A door with real EVIDENCE does not come through here.**  The statement
    matcher confirms a day it has a bank line for and states ``observed``
    directly (``statement_match._moving._apply_day``), which is how an
    ``asserted`` bound the bank agrees with is raised rather than frozen.  The
    difference is not the day, it is that one caller read a document and the
    other re-posted a form.

    Args:
        submitted_day: The civil day the form submitted.  Never ``None`` -- a
            submission with no day is not a statement about one, and each door
            answers that case before it gets here.
        recorded: What the row already records
            (:func:`recorded_settle_day`), or ``None`` when it records no day.

    Returns:
        *recorded* unchanged when *submitted_day* is an echo of it; otherwise a
        new :class:`SettleDay` on the ``entered`` basis.
    """
    if recorded is not None and recorded.day == submitted_day:
        return recorded
    return SettleDay(day=submitted_day, basis=SettledDayBasisEnum.ENTERED)


def record_settle_day(
    row: SettleDatedMixin, settle_day: "SettleDay | None",
) -> None:
    """Write *settle_day* onto *row*, or clear the pair when it is ``None``.

    **The ONE assignment of the pair**, so the two columns cannot be written
    apart by any door that goes through it.  Its storage-tier backstop is each
    table's ``ck_*_settle_day_basis_pairing`` -- a BICONDITIONAL, so a day
    without a basis AND a basis left behind with no day are both unstorable.

    **The biconditional was the difference from the FIGURE's pairing one
    column over, and it is deliberate** (developer, 2026-08-22).  The row's own
    figure columns carried a bare implication
    (``ck_transactions_settled_amount_needs_basis``, deleted with them at plan
    step ``balance:X-bi-4b-2``) because a revert RELEASES the day and KEEPS what
    moved, so a figure legitimately outlives the assertion that recorded it --
    as the covering movement, un-dated, does now.  The day and its basis have no
    such asymmetry: the basis describes the day, so the two share one lifetime,
    and the stronger constraint costs nothing and forbids the residue a revert
    would otherwise be free to leave.

    **It refuses a day the account's books do not reach** (plan step X-f3c-2b,
    finding **N-378**).  Where the books start holds the balance at the CLOSE
    of its day -- an opening record's ``opened_on``, or a configured loan's
    origination (card plan#2436, ruling **plan#3954**;
    :func:`app.services.cash_ledger.books_start`) -- so a movement dated on or
    before that day is already inside it and would be counted a second time.
    Asked BEFORE either column is assigned, so a refused call leaves the row
    untouched -- the same ordering
    :func:`app.services.status_seam.apply_status_change` keeps for its own
    refusals, and for the same reason.  The CLEAR arm is not bounded: dropping
    a day withdraws an assertion and states nothing about when money moved,
    which is the identical carve-out ``settle_day_for_status`` makes for the
    floor (ruling R-EG's unlock path).

    Mutates in place.  Does not flush or commit.

    Args:
        row: The transaction or purchase to write.  Its ``account_id`` is what
            the boundary above is asked about -- present on both tables, and
            on a MOVEMENT it is the account the money moved through (plan
            step ``credit_card:CC-5-1``, ruling **R-BAL75**), so a movement
            the card's doors write on the card (the purchase door since
            ``CC-5-2``, the settle verb's tender since ``CC-5-3``) is graded
            against the CARD's opening whatever account its parent row
            names.
        settle_day: What it now records, or ``None`` to clear both columns --
            which is what a revert, a cancel and an emptied date box each mean.

    Raises:
        ValidationError: When *settle_day*'s day is on or before the day the
            account's books start (from
            :func:`app.services.cash_ledger.reject_movement_before_books_open`).
    """
    if settle_day is None:
        row.settled_on = None
        row.settled_day_basis_id = None
        return
    reject_movement_before_books_open(row.account_id, settle_day.day)
    row.settled_on = settle_day.day
    row.settled_day_basis_id = settle_day.basis_id


def settle_day_from_columns(
    settled_on: "date | None", settled_day_basis_id: "int | None",
) -> Optional[SettleDay]:
    """Return the :class:`SettleDay` a stored PAIR of values means.

    **The ONE decode, over VALUES rather than over a row.**  It had a second
    caller holding the pair as values -- the transfer PATCH, which read the
    pair off the income shadow in ONE query because a ``Transfer`` carries
    neither column, and two property reads could straddle two rows and hand
    this a half-pair (adversarial review 2026-08-22).  Plan step
    ``balance:X-bi-6-4c-3`` gave each side its own day, read off the side's
    own movement, which is a row (:func:`recorded_settle_day`); the values
    shape stays because it is where the decode and its refusals live, and
    three modules cite it as the pattern for a caller holding values.

    Args:
        settled_on: The stored day, or ``None``.
        settled_day_basis_id: The stored ``ref.settled_day_bases`` id, or
            ``None``.

    Returns:
        The :class:`SettleDay` the pair states, or ``None`` when both are
        ``None``.

    Raises:
        KeyError: When *settled_day_basis_id* names no
            :class:`~app.enums.SettledDayBasisEnum` member.  Unreachable through
            the foreign key, which admits only the seeded rows; it is how a
            member ADDED without this map being extended fails loudly rather
            than silently reading as one of the three that exist.
        ValueError: When one of the two is ``None`` and the other is not.  Both
            halves are refused by each table's ``ck_*_settle_day_basis_pairing``,
            so reaching either means something wrote around every door -- and
            answering ``None`` for the first would hand a caller a row it could
            not classify while claiming it had no day at all.
    """
    if settled_on is None and settled_day_basis_id is None:
        return None
    if settled_on is None or settled_day_basis_id is None:
        raise ValueError(
            f"A settle day and its basis are one fact: settled_on="
            f"{settled_on!r} beside settled_day_basis_id="
            f"{settled_day_basis_id!r} is a half-written pair that "
            "ck_transactions_settle_day_basis_pairing (and its "
            "transaction_entries twin) makes unstorable. Something wrote "
            "around app.services.settle_day.record_settle_day."
        )
    member = {
        ref_cache.settled_day_basis_id(basis): basis
        for basis in SettledDayBasisEnum
    }[settled_day_basis_id]
    return SettleDay(day=settled_on, basis=member)


def recorded_settle_day(row: SettleDatedMixin) -> Optional[SettleDay]:
    """Return the settle day *row* records, or ``None`` when it carries none.

    The read half of the pair, and :func:`record_settle_day`'s inverse, for a
    row that CARRIES the two columns.  Its callers are the ones that must carry
    a row's day forward without inventing either term: the transfer writer,
    which reads each side's current day and borrows a sibling's for a side
    with no evidence (``transfer_service._status``, ruling **R-BAL142**); the
    statement matcher's candidate construction, which needs to know whether
    the day it is about to bound a bank line against is a point or an upper
    bound; and the three form doors, which need it to tell a prefill from a
    retype (:func:`submitted_settle_day`) -- the transfer popover's per side,
    off each leg's movement.

    Args:
        row: The transaction or purchase to read.

    Returns:
        The recorded :class:`SettleDay`, or ``None`` for a row with no settle
        day.

    Raises:
        KeyError: Propagated from :func:`settle_day_from_columns`.
        ValueError: Propagated from :func:`settle_day_from_columns`.
    """
    return settle_day_from_columns(row.settled_on, row.settled_day_basis_id)
