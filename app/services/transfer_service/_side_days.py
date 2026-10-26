"""
Shekel Budget App -- Transfer Service: EACH SIDE'S OWN DAY

A transfer leaves one account and arrives at another, and since plan step
``balance:X-bi-6-4c-3`` each side keeps its own "money moved on" day with a
basis saying how that day is known (ruling **R-BAL142**, amending **R-BAL89**).
A side with evidence of its own -- the bank showed it (``observed``), a balance
was asserted over it (``asserted``), or the owner typed it (``entered``) --
keeps that day.  A side with none BORROWS its other side's day, so it follows
when that day is corrected (``borrowed``); when neither side has one, both
borrow the day Paid was pressed.

This leaf is the whole derivation, and it is PURE: values in, values out, no
session, no clock (the writer reads ``display_today()`` once and passes it).
Three names carry the three rules, so each has ONE home:

* :func:`~app.services.settle_day.is_evidence` -- whether a day is the side's
  own (the REC-552 study's seam S1; it lives beside ``SettleDay`` because the
  status seam asks it too);
* :func:`borrowed_day` -- the day a side with no evidence takes (seam S6: every
  writer that derives a borrowed day reaches it through :func:`resolve_pair_days`,
  never an inline computation);
* :func:`resolve_pair_days` -- both sides at once, the rule itself.

:class:`SideDay` is what a door STATES ("the money moved on THIS account on
this day, known this way"), and :func:`stated_by_side` turns the doors' account
view into the writer's side view once, before any write.  The writer of every
STATED day is :func:`app.services.transfer_service._status.apply_status_to_all_three`,
which derives both sides' days through :func:`resolve_pair_days`.  One other
act writes a side's day, and states none: the endpoint move lands a moved
side's ``borrowed`` day (``status_seam.land_moved_side_record``), derived
through the same resolver (``_endpoints._moved_days``).

Each side's current day is read off its payment RECORD by the writer (plan
step ``balance:X-bi-6-4d-2``; off its twin row until then); a ``$0.00`` close
holds no record and so no day.  Nothing here reads either.
"""

from dataclasses import dataclass
from datetime import date
from typing import Iterable, NamedTuple

from app.enums import SettledDayBasisEnum
from app.services.settle_day import SettleDay, is_evidence


@dataclass(frozen=True)
class SideDay:
    """A day a door STATES for one side of a transfer, named by its account.

    **Named by ACCOUNT rather than by side**, because that is what every door
    knows: the statement matcher and the reconcile panel read one account's
    statement, and each popover box belongs to one account.
    :func:`stated_by_side` maps it onto the transfer's side against the
    endpoints the act leaves the transfer with.

    Attributes:
        account_id: The account the money moved on -- one of the transfer's
            two endpoints.
        day: The day and how it is known.  Never ``borrowed``: a borrowed day
            is DERIVED (:func:`borrowed_day`), never stated, so no door can
            hand one in and the "is this evidence" question is asked only of
            what is stored.
    """

    account_id: int
    day: SettleDay

    def __post_init__(self) -> None:
        """Refuse a value that states nothing or states a guess.

        Raises:
            ValueError: When :attr:`day` is ``None`` (a door that means to
                state nothing passes no :class:`SideDay` at all), or when its
                basis is ``borrowed``.  Both are programming errors at the
                call site: no form or statement produces either.
        """
        if self.day is None:
            raise ValueError(
                "A SideDay states a day for one side, so it cannot wrap None. "
                "A door that states no day passes no SideDay at all."
            )
        if not is_evidence(self.day):
            raise ValueError(
                "A borrowed day is derived from the other side, never stated: "
                "a door states what it KNOWS (observed, asserted or entered), "
                "and resolve_pair_days derives the rest."
            )


class PairDays(NamedTuple):
    """One transfer's two sides' days, expense side then income side.

    The ``(expense, income)`` order every pair of sides in this package is
    written in (``_status._side_legs``).  ``None`` on a side means "no day" --
    for a stated pair, that nothing was stated for that side; for a current
    pair, that the side holds no day in the settled band.
    """

    expense: SettleDay | None
    income: SettleDay | None


#: A pair stating nothing for either side.
NO_DAYS = PairDays(expense=None, income=None)


def borrowed_day(lender: SettleDay | None, fallback: date) -> SettleDay:
    """Return the day a side with no evidence of its own takes.

    **The ONE derivation of a borrowed day** (the REC-552 study's seam S6):
    the other side's day when that side has one of its own, else *fallback* --
    the day the pair already shares, or the day Paid was pressed.  Every writer
    that (re)derives a borrowed day reaches it through
    :func:`resolve_pair_days`, so a rule that later clamps a borrowed day (the
    study's loan-side answers) has one place to go.

    Args:
        lender: The other side's own day, or ``None`` when it has none.
        fallback: The day to borrow when there is no lender
            (:func:`repair_fallback`).

    Returns:
        A ``borrowed`` :class:`SettleDay`.
    """
    return SettleDay(
        day=lender.day if lender is not None else fallback,
        basis=SettledDayBasisEnum.BORROWED,
    )


def repair_fallback(
    recorded_in_repair_order: Iterable[SettleDay | None], today: date,
) -> date:
    """Return the day a pair with no evidence on either side shares.

    The first day recorded in the repair order -- expense before income --
    and otherwise *today*.  So a mark-paid borrows today, a re-derivation
    never invents a day when either side records one, and two borrowed sides
    share one day.  (The order put a twin still in the settled band before
    one that had drifted out of it until plan step ``balance:X-bi-6-4d-2``:
    a transfer has one status, so there is no drift left to order.)

    Args:
        recorded_in_repair_order: Each side's recorded day, expense then
            income.
        today: The fallback when neither records one -- the writer's own
            (``_status._fallback_day``).

    Returns:
        The day to share.
    """
    for recorded in recorded_in_repair_order:
        if recorded is not None:
            return recorded.day
    return today


def stated_by_side(
    side_days: "tuple[SideDay, ...]",
    from_account_id: int,
    to_account_id: int,
) -> PairDays:
    """Map what the doors stated by ACCOUNT onto the transfer's two SIDES.

    Asked before the first write of the act, against the endpoints the act
    LEAVES the transfer with -- ``_update`` reads them off
    ``_resolve_endpoints``, because an endpoint move assigns relationships and
    the transfer's own account columns name the old accounts until the flush
    after the move.

    Args:
        side_days: What the door stated; empty when it stated nothing.
        from_account_id: The account the money leaves (the expense side).
        to_account_id: The account it arrives at (the income side).

    Returns:
        The stated days by side.

    Raises:
        ValueError: When a :class:`SideDay` names an account on neither side,
            or two name the same side.  Both are a door pairing a transfer
            with the wrong account -- a programming error, not user input.
    """
    by_side: dict[str, SettleDay] = {}
    for side_day in side_days:
        if side_day.account_id == from_account_id:
            side = "expense"
        elif side_day.account_id == to_account_id:
            side = "income"
        else:
            raise ValueError(
                f"A day was stated for account {side_day.account_id}, which is "
                f"neither side of this transfer ({from_account_id} -> "
                f"{to_account_id})."
            )
        if side in by_side:
            raise ValueError(
                f"Two days were stated for account {side_day.account_id}; a "
                "side has one day."
            )
        by_side[side] = side_day.day
    return PairDays(
        expense=by_side.get("expense"), income=by_side.get("income"),
    )


def resolve_pair_days(
    current: PairDays, stated: PairDays, fallback: date,
) -> PairDays:
    """Return each side's day once this act lands (ruling **R-BAL142**).

    1. A side's OWN day is the day stated for it now, else its current day
       when that day is evidence
       (:func:`~app.services.settle_day.is_evidence`).  A stated day always
       applies to its side; the VERB decides which stated days reach here.
    2. Both own: each keeps its own.  One own: the other borrows it.  Neither:
       both borrow *fallback* (:func:`borrowed_day`).

    So a figure correction or an untouched Save states nothing and moves no
    side that has evidence (finding **N-304**), a statement on one side
    re-dates only that side while a borrowing sibling follows, and nothing
    orders the two sides' days against each other (the study's seam S4: a
    lender may pull a payment before the borrower's account shows it).

    Args:
        current: Each side's day as it stands, ``None`` for a side holding no
            dated record (and for a side an endpoint move is carrying, whose
            evidence ``_endpoints._moved_days`` drops).
        stated: What the act states, already admitted by its verb.
        fallback: The day to share when neither side has evidence
            (:func:`repair_fallback`).

    Returns:
        Both sides' days.
    """
    expense = _own(current.expense, stated.expense)
    income = _own(current.income, stated.income)
    if expense is not None and income is not None:
        return PairDays(expense=expense, income=income)
    if expense is not None:
        return PairDays(expense=expense, income=borrowed_day(expense, fallback))
    if income is not None:
        return PairDays(expense=borrowed_day(income, fallback), income=income)
    # Asked once PER SIDE, although both answer *fallback* today: a borrowed
    # day is derived for its own side, so an input that later reaches one
    # side's derivation alone (the REC-552 study's loan-side answers) needs no
    # second producer.
    return PairDays(
        expense=borrowed_day(None, fallback),
        income=borrowed_day(None, fallback),
    )


def _own(current: SettleDay | None, stated: SettleDay | None) -> SettleDay | None:
    """Return a side's own day: stated now, else its current day if evidence."""
    if stated is not None:
        return stated
    if current is not None and is_evidence(current):
        return current
    return None
