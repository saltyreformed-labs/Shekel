"""
Shekel Budget App -- Status Seam: the MECHANICS

:func:`apply_status_change` -- the ONE place a ``Transaction.status_id`` or a
``Transfer.status_id`` may be assigned -- and the two readings that grade what a
FORM submission means for the status it carries.

**This leaf is what the ``shekel-transaction-status-bypass`` fence allowlists**,
and the narrowing is the point rather than a tidy-up.  The entry read
``app.services.status_seam`` while that was one module; :func:`._common._module_in_allowlist`
matches a package PREFIX, so the split would have extended the exemption over
every leaf here without anybody deciding it should be.  That is precisely what
happened to ``transfer_service`` at plan step X-f2-c3, and its ``_status``
docstring records it.  Only this module writes ``status_id``; the record leaf
and the refusals leaf write nothing at all.

Split out of the single ``status_seam`` module at plan step **X-au-c3**; see
:mod:`._record` for the ground the split was made on.

Mutates the passed row in place and does NOT commit -- the caller owns the
session boundary.  It flushes EXPLICITLY only where the covering movement's
own writes must (``_covering``: a payment re-pointed, or taken off the books
through ``movement_removal``); a lazy load of ``row.entries`` may autoflush
outside a caller's ``no_autoflush``.  No Flask imports.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from app.enums import SettledDayBasisEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import pay_period_service
from app.services.match_press import Press
from app.services.planned_rows_books import reject_revert_below_the_books
from app.services.settle_day import (
    SettleDay,
    record_settle_day,
    submitted_settle_day,
)
from app.services.state_machine import verify_transition
from app.services.stated_figure import StatedFigure
from app.services.status_seam._record import Settlement
from app.services.status_seam._covering import sync_covering_movement
from app.services.status_seam._refusals import (
    StatusBearingRow,
    reject_figure_without_settled_status,
    reject_future_settle_day,
    reject_settle_day_without_settled_status,
    reject_settlement_on_a_deleted_row,
    reject_settlement_without_settled_status,
    reject_stated_figure_over_purchases,
    reject_tender_without_settled_status,
)
from app.utils.balance_predicates import (
    enters_settled_band,
    settled_status_ids,
)
from app.utils.dates import display_today


def settle_day_for_status(
    user_id: int,
    new_status_id: int,
    submitted_day: Optional[date],
    recorded: Optional[SettleDay] = None,
) -> Optional[SettleDay]:
    """Return the settle day a FORM submission means, and refuse an impossible one.

    **The edit doors' half of the settled-iff-dated invariant, stated once**
    (plan step X-f1c, ruling **R-EG**).  Both full-edit forms submit the row's
    whole state on Save, and the documented way to unlock a finalised row is to
    set Status to Projected in that same form -- so a revert arrives as
    ``{status_id: Projected, settled_on: <the day the row already carried>}``.
    That day is a stale ECHO of the state being left, not an assertion that
    money moved: the user picked Projected, which says it did not.  The seam's
    own :func:`reject_settle_day_without_settled_status` refuses that pair with
    a 400 -- correctly, for a SERVICE caller, which is asserting both facts on
    purpose -- and applying that refusal to a form submission would make the
    only documented unlock path fail on every settled row.

    So the rule is: **a day counts only when the row is moving INTO (or staying
    in) the settled band.**  Anywhere else it is dropped and the seam clears the
    column, which is what a revert means.

    It lives here, beside the refusal it defers to, rather than in either route:
    the transaction PATCH and the transfer PATCH are two doors onto ONE rule,
    and two spellings of it is this arc's own root cause 1.  It does NOT weaken
    the service guard -- the seam still refuses a day handed to it for an
    unsettled status, so a programmatic caller that means it still fails loud.

    **It also enforces the FLOOR (ruling R-EL), and the floor is a DOOR rule
    rather than a seam invariant -- which is the whole reason it is here and not
    in** :func:`apply_status_change`.  :func:`reject_future_settle_day` refuses a
    day in the future from ANY caller, because no caller can legitimately record
    money that has not moved.  The other end is not like that.  A day at or
    before an assertion is ABSORBED into it by ``cash_ledger._walk``, which then
    resets the running total to the asserted balance -- and for a GENUINE
    pre-schedule settle that is CORRECT (ruling R-EB: an assertion reconciles, so
    anything before it is already inside the asserted balance).  Recording money
    that moved before you started budgeting is a real thing to do, and a bank
    import would do it in bulk.  **What is never legitimate is a HUMAN typing a
    year wrong**, so the bound belongs where a human types.

    Measured: enforcing it at the seam instead refused **six** existing tests
    whose scenario is a payment budgeted to a 2026 pay period whose cash moved in
    December 2025 -- the year-boundary attribution rule, and exactly the shape an
    import produces.  Enforcing it here refuses none of them, because they call
    the service.

    ``fields.Date()`` deserializes ``"0202-08-04"`` to a real ``date``, so the
    typo is an ordinary slip in a box whose own tooltip invites correction.
    Worked on a ``$1,000`` anchor observed three days ago with a ``$100`` settled
    expense: the grid reads ``$900``, and after the typo ``$1,000``, with the
    ``$100`` becoming an unexplained plug at the next anchor re-derive -- the row
    still reading Paid throughout.  The bound is
    :func:`app.services.pay_period_service.earliest_recordable_day`, the SAME
    floor an anchor's ``observed_on`` has used since finding **N-133**, and both
    settle-day inputs carry it as ``min``.

    **Only a FORWARDED day is bounded.**  A day dropped beside a revert writes
    nothing, so refusing it would break the unlock path R-EG exists to keep open.

    Args:
        user_id: The row owner, whose pay-period schedule sets the floor.
        new_status_id: The ``ref.statuses.id`` the row is moving to -- the
            SUBMITTED status when the form carried one, else the row's current
            one (an edit that changes only the day is an identity transition).
        submitted_day: The civil day the form submitted, or ``None`` when it
            submitted none.  An empty date input loads as absent rather than as
            an explicit ``None`` (the field is not ``allow_none``), because a
            settled row always carries a day: the way to remove one is to leave
            the settled band, which clears it.
        recorded: What the row ALREADY records
            (:func:`app.services.settle_day.recorded_settle_day`), or ``None``
            when it records no day.  **It is what makes this function
            ECHO-AWARE**, which is exactly why :func:`figure_for_status` takes
            the row beside it: without the stored value a door cannot tell an
            untouched prefill from a day the user just retyped, and both
            popovers prefill the control.  Defaulting to ``None`` treats every
            submission as a fresh assertion, which is the SAFE direction for a
            caller that genuinely has no stored pair -- a row settling for the
            first time -- and the wrong one for a re-save, which is why both
            route doors pass it.

    Returns:
        A :class:`~app.services.settle_day.SettleDay` on the ``entered`` basis
        wrapping *submitted_day* when *new_status_id* is settled -- which is
        ``None`` itself when the form submitted none, and that is the same
        answer the unsettled branch gives; ``None`` otherwise.  The seam reads
        ``None`` as "derive the day from the status", i.e. preserve on a
        re-settle and clear on a revert.

        **The basis is ``entered`` for a day that MOVED, and *recorded*'s own
        basis for one that did not** (plan step X-az). A day that arrived in a
        date box and differs from the stored one is the owner's own record: no
        bank statement showed it and no balance assertion bounds it, which is
        exactly what :attr:`~app.enums.SettledDayBasisEnum.ENTERED` names.  A
        day EQUAL to the stored one is the control coming back untouched and
        asserts nothing, so the row's existing basis stands --
        :func:`app.services.settle_day.submitted_settle_day` is that rule, and
        it carries the ``$4,173.07`` this function cost production without it.
        Stating it there rather than at the three routes is the same argument
        that put the rest of this function here -- the transaction PATCH, the
        transfer PATCH and the entry PATCH are doors onto ONE rule.

    Raises:
        ValidationError: When the day would be FORWARDED and precedes the
            owner's earliest recordable day (ruling R-EL).  A 400: it is
            ordinary user input from the correction box.

    Note:
        **The ``submitted_day is None`` arm was forbidden here until plan step
        X-az and is load-bearing now**, and the reversal is worth stating
        because the rule behind the old prohibition has not changed.  It read:
        the arm is unreachable as a DECISION -- both branches already answer
        ``None`` for a ``None`` day -- so no single-line mutation of this
        function could fail a test written against it, and a guard whose only
        possible test cannot fail is not a guard (finding **N-184**).  That was
        exact while the settled branch returned the day it was handed.  It now
        returns a :class:`~app.services.settle_day.SettleDay`, which cannot
        wrap a ``None``, so the two branches answer differently and deleting
        this line raises rather than returning the same value by another route.
        The test that fails when it goes is a settled status with no submitted
        day -- the ordinary re-settle, which must reach the seam as "derive the
        day" and not as a malformed value.
    """
    if new_status_id not in settled_status_ids():
        return None
    if submitted_day is None:
        return None
    floor = pay_period_service.earliest_recordable_day(user_id)
    if submitted_day < floor:
        raise ValidationError(
            f"A settle day of {submitted_day.isoformat()} is before this "
            f"budget's schedule starts ({floor.isoformat()}).  Check the "
            "year -- or generate earlier pay periods first if the money "
            "really moved then."
        )
    # **The floor is asked BEFORE the echo rule, and the order is deliberate.**
    # It preserves exactly today's refusal on a legacy row whose stored day
    # precedes the schedule: such a row 400s on any save, echo or not, and
    # loosening that is not this step's to decide.  What the echo rule changes
    # is only WHICH BASIS a surviving day records.
    return submitted_settle_day(submitted_day, recorded)


def figure_for_status(
    row: StatusBearingRow,
    new_status_id: int,
    submitted: Optional[StatedFigure],
    recorded: Optional[Decimal],
) -> Optional[StatedFigure]:
    """Return the figure a SUBMISSION means, or refuse a real conflict.

    **The figure's half of what :func:`settle_day_for_status` does for the day,
    and it is ECHO-AWARE where that one is status-only** (developer ruling
    2026-08-17; the echo term added 2026-08-18 after a neutral review measured
    what its absence cost).

    Both full-edit forms submit the row's WHOLE state on Save, so a settled row
    reverting to Projected posts the Actual box's current contents alongside
    ``status_id = Projected``.  When the box was NOT touched that figure is a
    stale ECHO of the state being left, and keeping it would make the documented
    unlock path fail on every settled row -- the trap ruling **R-EG** removed
    for the settle day.  So an echo is dropped.

    **A DIFFERENT figure beside an unsettling status is not an echo, and
    dropping it was a measured money defect.**  The first version of this rule
    read the status alone: it took no row, so it could not tell a prefill from a
    number the user had just retyped, and it discarded both.  Measured end to
    end -- a bill settled at a hand-typed ``$245.32``, re-read off the statement
    as ``$214.37``, corrected and reverted in one Save: HTTP 200, no message,
    and the row still recording ``$245.32``, which is what
    :meth:`Settlement.from_settle` then re-books.  Worse than the day's
    analogue, because a revert CLEARS the day and RETAINS the figure, so a
    silent drop there becomes a silently wrong booking here.

    Such a submission asserts two contradictory things -- "this much moved" and
    "it did not move" -- so it is REFUSED, in the sentence
    :func:`reject_figure_without_settled_status` carries, which tells the user
    to correct the figure and revert as two acts.  That refusal was unreachable
    from any HTTP door until this term existed.

    Args:
        row: The row the submission is about, for the refusal's noun.  For a
            transfer that is the PARENT, whose money moves on its legs.
        new_status_id: The ``ref.statuses.id`` the row is moving to -- the
            SUBMITTED status when the form carried one, else the row's own (an
            edit that changes only the figure is an identity transition).
        submitted: The figure the form submitted and who wrote it
            (:class:`~app.services.stated_figure.StatedFigure`), or ``None``
            when it submitted none.  Only the FIGURE is graded here; the
            source rides through untouched, because an echo is decided by
            the number and a refusal by the status.
        recorded: What the row RECORDS as having moved
            (:func:`app.services.row_valuation.settled_figure`), which is what
            the box was prefilled from -- so equality here is exactly "the user
            did not touch the box".  For a transfer it is the figure the
            transfer stores, read through its expense leg
            (:func:`~app.services.row_valuation.leg_settled_figure`).

    Returns:
        *submitted* when the row is settling or staying settled; ``None`` when
        nothing was submitted, or when what was submitted is an echo.

    Raises:
        ValidationError: When a figure DIFFERING from the record arrives beside
            a status that settles nothing.  A 400 at either route.
    """
    if submitted is None:
        return None
    if new_status_id not in settled_status_ids():
        if submitted.amount == recorded:
            return None
        reject_figure_without_settled_status(row, new_status_id)
    return submitted


def tender_for_status(
    row: Transaction,
    new_status_id: int,
    submitted_account_id: Optional[int],
    recorded_account_id: int,
) -> Optional[int]:
    """Return the TENDER a submission means, or refuse a real conflict.

    :func:`figure_for_status`'s twin for the record's third fact (plan step
    ``credit_card:CC-5-3``): the full-edit popover posts its "Paid from"
    picker on every Save, preselected from what the row records
    (:func:`~._record.tender_account_id_of`), so an untouched picker is an
    ECHO -- dropped whatever the status, because a re-statement of the
    account the record already names is not a correction and must not turn
    an identity Save into a re-point.  A DIFFERENT account beside a status
    that settles nothing asserts two contradictory things -- "it was paid
    from the card" and "it did not move" -- and is REFUSED
    (:func:`~._refusals.reject_tender_without_settled_status`), never
    dropped: the figure's twin measured what a silent drop costs.

    Args:
        row: The row the submission is about, for the refusal's sentence.
        new_status_id: The ``ref.statuses.id`` the row is moving to -- the
            SUBMITTED status when the form carried one, else the row's own.
        submitted_account_id: The account the form named, or ``None`` when it
            carried no picker (one member: ruling **R-CC34**).
        recorded_account_id: What the row records as its tender -- the
            kept movement's account, else the row's own -- which is what the
            picker was preselected from, so equality here is exactly "the
            user did not touch it".

    Returns:
        *submitted_account_id* when it names an account other than the
        recorded one and the row is settling or staying settled; ``None``
        when nothing was submitted or the submission is an echo.

    Raises:
        ValidationError: When an account DIFFERING from the record arrives
            beside a status that settles nothing.  A 400 at either route.
    """
    if submitted_account_id is None or submitted_account_id == recorded_account_id:
        return None
    reject_tender_without_settled_status(row, new_status_id)
    return submitted_account_id


def apply_status_change(
    row: StatusBearingRow,
    new_status_id: int,
    *,
    settle_day: Optional[SettleDay] = None,
    settlement: Optional[Settlement] = None,
    press: Press | None = None,
) -> None:
    """Apply a status transition -- the single status seam, for either row type.

    The ONE place a ``Transaction.status_id`` or a ``Transfer.status_id`` may be
    assigned.  Every status-changing path -- the manual ``mark_done`` branch,
    the inline PATCH, ``cancel``, ``mark_as_credit`` / ``unmark_credit``, the
    envelope ``transaction_service.settle_from_entries``, and a transfer's one
    status writer (``transfer_service._status.apply_status_to_all_three``) --
    routes through here so the status mechanics are uniform and impossible to
    skip.

    Does the status MECHANICS only, in order:

      1. ``verify_transition`` -- the state-machine legality gate, which picks
         the workflow from *row*'s own model class; raises ``ValidationError``
         on an illegal move (e.g. Cancelled -> Paid), which the route layer
         surfaces as a 400.  Then, for a ``Transaction``, the revert refusal
         (:func:`~app.services.planned_rows_books.reject_revert_below_the_books`,
         rulings **R-PC97** and **R-PC99**): a revert to Projected that the
         books hold -- the books of the account the row sits on holding its
         own day, or its definition's dropping its occurrence -- raises
         ``ValidationError`` before anything is written.
      2. assign ``status_id``.
      3. maintain the SETTLEMENT RECORD -- ``settled_on``,
         ``settled_day_basis_id``, the clearing link and the COVERING MOVEMENT
         that carries what moved -- as ONE act (see the *settle_day* and
         *settlement* args).  **Transactions only**, because
         ``Transfer`` carries none of those columns: a transfer's figure is its
         own and each side's day and link are that side's payment record's,
         both written by the seam's Transfer arm (``_side.sync_side_records``)
         before the transfer writer calls this, so a transfer settle still
         records what moved and when.
      4. ``db.session.expire(row, ["status"])`` so a pre-commit reader (a cell
         render, a test assertion) sees the new ``Status`` row, not the stale
         ``lazy="joined"`` one -- the exact trap ``mark_as_credit`` documented
         and handled inline before this seam absorbed it.  Both models declare
         ``status`` as ``lazy="joined"``, so both need it; the transfer path did
         not do this before X-aj1 and held only because every route commits
         before it renders.

    It deliberately does NOT reconcile the ledger and does NOT commit:
    ledger emission is reconciled at the END of each handler, after every
    effect field is applied, never at the status flip (Build-Order Step 3,
    Commit 6 -- the same placement ``transfer_service.update_transfer`` uses;
    the PATCH handler's UNLOCK order, ruling **R-BAL58**, runs the status verb
    before its field writes and reconciles again after them); the caller owns
    the session boundary.  **The two exceptions are the covering movement's
    own**, and each may FLUSH: a payment a ``$0.00`` or ``purchases`` record
    takes off the books has its legs reversed and its matches withdrawn
    before it is deleted (``_covering._withdraw`` through
    ``movement_removal.remove_movements``, plan step
    ``credit_card:CC-5-4a-3``), because those legs and members must go while
    the rows they link still exist; and a re-pointed payment leaves its
    matches before its account moves (``_covering._re_point``), because the
    member's composite key would refuse the account change while it stands.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` whose status changes.  Must
            be session-attached so the ``status`` expire reloads; its
            ``status_id`` is read as the current state for the transition check
            and its CLASS selects the workflow.
        new_status_id: The ``ref.statuses.id`` to move to.
        settle_day: Settle-day policy, read only for a ``Transaction``.
            ``None`` (the default) DERIVES the day from *new_status_id*: stamp
            ``display_today()`` on entering a settled status (Paid or
            Received) that has none yet, **preserve an existing one on an
            idempotent re-settle**, and clear it on entering a non-settled
            status (so a reverted / cancelled / credited row drops its stale
            settle day and the basis that described it).  A non-``None``
            :class:`~app.services.settle_day.SettleDay` is written verbatim,
            and it states BOTH the day and HOW that day is known.

            **The basis is the CALLER's to state and there is no default**
            (plan step **X-az**, finding **N-332**).  A door that knows a day
            knows where it came from: the statement matcher writes a day the
            bank POSTED (``observed``), the reconcile panel writes the day a
            BALANCE was asserted for and so an UPPER BOUND (``asserted``), and
            an edit box writes the owner's own (``entered``).  ``settled_on``
            carried all three with nothing saying which, and the matcher told
            the panel's apart by testing whether ``reconciled_by_id`` was
            populated -- exact over those three writers, blind to the third of
            them, and one new writer from wrong.  Packaging the day WITH its
            basis is what leaves no default to get wrong: a caller cannot pass
            a day without saying what kind it is.

            **A day supplied for a NON-settled status is REFUSED**
            (:func:`reject_settle_day_without_settled_status`, finding
            **N-183**), which is what makes "a row is settled if and only if it
            carries a settle day" a property of this function rather than a
            convention its callers keep.

            **The day is the USER's, not the server's** (ruling R-DH (b)).
            ``display_today()`` reads the display timezone, where
            ``date.today()`` would read the process's UTC day and file an
            8pm-Eastern settle under tomorrow.  This is also why the seam no
            longer assigns ``db.func.now()``: that reached PostgreSQL's clock,
            one of the four database-clock reaches finding N-65 had to build
            ``_freeze_db_clock`` to contain, and a Python ``date`` is a value
            the suite's own clock freeze already governs.

            **The preserving rule is load-bearing and is finding N-146's fix.**
            The transfer service's deleted seam re-stamped every entry into a
            settled status including an identity re-submit, and since plan step
            E1a a settle's civil day IS the ``entry_date`` its postings are
            filed under -- so editing the notes on a paid transfer moved its
            money to today.  **Finding N-178 then showed the seam alone is not
            enough**: both mark-done routes passed an explicit instant, which
            overrode this rule, and a replayed POST re-dated a settled transfer
            by however long ago it really settled.  Neither route passes one
            now, so the preserve rule is the only rule.

            **A RE-SETTLE is a RE-ENTRY, not a first entry**, and that is why
            preservation matters beyond the edit forms: re-submitting a row's
            own settled status -- which the full-edit popover does on every
            Save, since it posts the whole row and not a delta -- must not move
            its money to the day the form was saved.

            The sharpest case used to be ``Paid -> Settled``, the ARCHIVE: it
            was a genuine STATUS CHANGE that still had to preserve the day.
            Plan step **X-am** deleted that status, so every within-band move
            left is the identity one, and preservation is what makes an
            untouched Save a no-op instead of a re-dating.

        settlement: WHAT moved, when this change RECORDS a settle
            (:class:`Settlement`).  Written as the row's COVERING MOVEMENT
            (``status_seam._covering.sync_covering_movement``) in the same act
            as the day above, so one call states everything a settle knows;
            the movement carries the figure and who wrote it, and it is the
            record's ONE home since plan step ``balance:X-bi-4b-2`` deleted
            the row's own ``settled_amount`` / ``settled_basis_id`` (ruling
            **R-BAL80**).  The settle DAY is deliberately not paired with it,
            because a revert withdraws the day and keeps what moved.

            **A row ENTERING the settled band must supply one**, and that
            refusal is what makes "a settled row states what moved" a property of
            this function rather than a convention its callers keep.  Before plan
            step X-au-c3 a settle recorded a figure only when a human had typed a
            correction, so most settled rows recorded nothing and every reader
            fell back to the row's PLAN -- and because a plan is a derivation,
            the plan then had to be FROZEN at settle so a later price change
            could not move a figure the bank had already taken.  A mandatory
            record is what leaves nothing to freeze.

            ``None`` leaves whatever the row already records untouched -- which
            is what an identity re-submit, a settle-day correction, an archive
            and a REVERT each mean.  A revert releases the ASSERTION
            (``settled_on``, ``settled_day_basis_id`` and ``reconciled_by_id``,
            above) and keeps what moved, because the two are different facts
            with different lifetimes; the comment at that assignment carries the
            argument.
        press: The save's :class:`~app.services.match_press.Press` (ruling **R-CC135**), over
            what the door's page NAMED -- the bank lines a ``$0.00`` or
            ``purchases`` record or a re-point would free -- or what lets the
            door stay silent (plan step ``credit_card:CC-5-4a-5``, rulings
            **R-CC81** / **R-CC127**).  It rides to the act that takes the
            payment out of its matches, which refuses a line the page did not
            name, and the save's close compares the rest.  It is a PRESS
            argument and not a field of *settlement*, which is also a value
            read back off a movement.  Defaults to ``None``, Josh's own
            words: *"A button with no warning sends nothing"* -- so a door
            that says nothing frees nothing, and is refused if it would.

    Raises:
        ValidationError: If the transition is illegal for *row*'s workflow
            (propagated from ``verify_transition``), if *settle_day* is
            supplied for a *new_status_id* that is not settled (propagated from
            :func:`reject_settle_day_without_settled_status`), if
            *settlement* is (propagated from
            :func:`reject_settlement_without_settled_status`), if *row* is a
            deleted ``Transaction`` and *settlement* would record money under
            it (:func:`reject_settlement_on_a_deleted_row`, ruling
            **R-CC89**), or if a ``Transaction``'s revert to Projected lands
            inside its books (propagated from
            :func:`~app.services.planned_rows_books.reject_revert_below_the_books`,
            ruling **R-PC97**), or if the payment this act takes out of its
            matches would free other bank lines than *press*'s page named (ruling
            **R-CC127**).
        ValueError: If a ``Transaction`` ENTERS the settled band with no
            *settlement*.  A programming error at the call site -- no form can
            express it -- so it is not a ``ValidationError``.
        TypeError: If *row* is not a status-bearing model (propagated from the
            state machine -- a programming error at the call site).  The
            ``datetime`` refusal (finding **N-179**) is no longer raised HERE:
            it moved to :class:`~app.services.settle_day.SettleDay`'s
            constructor at plan step X-az, which runs at the CALLER before this
            function is entered -- strictly earlier than a guard at the top of
            this body, and it keeps the "a refused call leaves the row
            untouched" property for free.
    """
    # **The ``datetime`` refusal used to be the first statement here and is now
    # a CONSTRUCTOR invariant** (finding **N-179**, moved at plan step X-az).
    # Its rule still lives once, on the column
    # (:func:`app.models.mixins.reject_settle_instant`, wired as an ORM
    # validator), and :class:`app.services.settle_day.SettleDay` calls it in
    # ``__post_init__`` -- so a caller cannot even PACKAGE an instant for this
    # door, let alone hand one over.  That is strictly earlier than a guard at
    # the top of this body, which is what the deleted call was here to buy: a
    # refused call must leave the row untouched, and one that never reaches this
    # function trivially does.
    #
    # The other half of the settled-iff-dated invariant, checked here so EVERY
    # caller inherits it (finding **N-183**).  Without it the explicit arm below
    # writes a day onto whatever status it is handed, which is how
    # ``transfer_service.update_transfer`` could date a Projected transfer; the
    # invariant then held only because no caller happened to do it.  Ordered
    # before ``verify_transition`` because a rejected call must not have mutated
    # the row.
    reject_settle_day_without_settled_status(new_status_id, settle_day)

    # A day that has not happened yet is refused (ruling **R-EJ**), ordered with
    # the refusals around it for the same reason: a rejected call must leave the
    # row untouched.  See :func:`reject_future_settle_day` for the measurement --
    # a future-dated settle puts already-spent money back in the balance.
    reject_future_settle_day(settle_day)

    # The settlement record's own half of the same invariant (plan step
    # X-au-c3): a row records what moved only while it is settled.  Ordered with
    # the refusals above, and for the identical reason.
    reject_settlement_without_settled_status(new_status_id, settlement)

    # A deleted row takes no money (ruling **R-CC89**): the record is what the
    # covering writer below turns into a payment under the row, so it is
    # refused here, ahead of any mutation like the three above.  **It reads
    # ``is_deleted`` as the request's owner lock left it** (plan step
    # ``balance:X-bn``, ruling **R-CC106**): the popover's Actual correction
    # and another tab's Delete of its row run one after the other, so a Delete
    # that came first is committed before the request read the row, and the
    # route's door answers it in the Save's words (ruling **R-CC105**) before
    # the view runs; only a service caller that skipped that door meets this
    # refusal (:func:`reject_settlement_on_a_deleted_row`).  Until that step this line was
    # preceded by the row's own lock (ruling **R-CC96**), without which the
    # correction read the row as live and met the delete's moved version as a
    # ``StaleDataError`` (measured 2026-09-23).
    reject_settlement_on_a_deleted_row(row, settlement)

    # (``reject_settle_day_without_a_record`` stood here through plan step
    # ``balance:X-bi-4b-1`` -- ``ck_transactions_settle_day_needs_a_record``
    # said in words, for the legacy row that recorded nothing; a settled row
    # IS its record since ``X-bi-4b-2``, so the state it refused has no
    # spelling, and both went with the row's figure columns.)
    if isinstance(row, Transaction):
        # The purchases ARE the figure (ruling **R-BAL78**, plan step
        # ``balance:X-bi-4a``): a stated figure beside real purchases would be
        # mirrored as a second movement over the same money.  Refused here,
        # ahead of any mutation like the four above it, so every door that
        # hands over a record inherits it; only a ``Transaction`` can hold
        # purchases (a transfer side holds its payment record alone:
        # ``ck_transaction_entries_side_link_is_a_record``).
        reject_stated_figure_over_purchases(row, settlement)

    # Read BEFORE the assignment below, because it is a question about the
    # status the row is LEAVING.  A row entering the settled band owes a record
    # of what moved; a row already in it keeps the one it has, which is what
    # makes an identity re-submit and a settle-day correction legal without one.
    entering = enters_settled_band(row, new_status_id)
    if entering and settlement is None and isinstance(row, Transaction):
        raise ValueError(
            f"Transaction {row.id} is entering the settled band with no "
            "settlement record. A settle states what moved as well as when: "
            "pass settlement=Settlement(...). Writing the status alone would "
            "leave the row settled with no covering movement, which every "
            "reader values as a close of nothing (ruling R-BAL82) -- and "
            "before plan step X-au-c3 it was worse, because the reader fell "
            "back to the row's PLAN and published a forecast as a fact."
        )

    verify_transition(row, new_status_id)
    if isinstance(row, Transaction):
        # A revert to Projected that the books hold is refused (rulings
        # **R-PC97** and **R-PC99**), ahead of any mutation like the refusals
        # above.  A TRANSFER is asked at its own one status door,
        # ``transfer_service.apply_status_to_all_three``, before either side's
        # record is written; asked here as well, one revert would walk its
        # definition twice.
        reject_revert_below_the_books(row, new_status_id)
    # Read BEFORE the assignment, for the covering movement below: whether
    # this act moves the row's assertion at all is a question about the
    # status the row is LEAVING as well as the one it enters.
    was_settled = row.status_id in settled_status_ids()
    row.status_id = new_status_id

    # Settle-day maintenance -- the day AND the basis that says how it is known,
    # written as one pair by ``settle_day.record_settle_day`` (plan step
    # **X-az**).  An explicit day wins; otherwise derive from the new status:
    # clear when leaving the settled band, stamp the user's today on the first
    # entry into it, and leave an existing day untouched on a re-settle (so
    # editing a Paid row -- which re-submits its unchanged status_id -- never
    # churns the day its money moved).  Skipped whole for a Transfer, which has
    # neither column; each side's payment record carries its own, written by
    # the seam's Transfer arm (``_side``).
    #
    # The three arms are exhaustive and the invariant falls out of them: the
    # guard above has already refused an explicit day on a non-settled status,
    # so arm 1 fires only INTO the settled band, arm 2 clears whenever the row
    # leaves it, and arm 3 fills the band's first entry.  A settled row always
    # leaves here dated with a basis and a non-settled row always leaves here
    # with neither, which is what
    # ``ck_transactions_settle_day_basis_pairing`` refuses to store otherwise.
    if isinstance(row, Transaction):
        # **Any MOVE of the settle day releases the clearing fact** (plan step
        # X-f3a-1, ruling **R-FL**), and the three arms below are exactly the
        # three ways it can move.  ``reconciled_by_id`` records that a named
        # statement was seen to show this money ON that day: a revert says the
        # money did not move at all, and a correction says it moved on a
        # different day, and neither leaves the observation standing.
        #
        # **It is not tidiness, it is what keeps the ledger renderable.**  A link
        # whose day the date rule would not pick cannot be folded while an
        # assertion RESETS the balance -- the fold emits the source on its
        # settle day and the correction on the statement's, so the balance stops
        # equalling what the user asserted (``StatementCoverage``'s
        # ``_recorded_anchor_id`` carries the theorem and the production
        # figure).  ``ck_transactions_cleared_needs_settle_day`` catches only the
        # NULL half of that; this catches the move.
        #
        # Re-settling afterwards is a fresh act, which the reconcile panel
        # records against whatever statement is current then.
        # **The release is about the DAY, never about the BASIS** (plan step
        # X-az).  ``reconciled_by_id`` records that a named statement was seen
        # to show this money ON a named day; re-stating the same day on a
        # better-known basis does not contradict that -- it agrees with it.
        # That is exactly what the statement matcher does when a bank line
        # CONFIRMS a day the reconcile panel had recorded as an upper bound:
        # the day is unchanged, the basis rises from ``asserted`` to
        # ``observed``, and the observation the link records still stands.
        released = settle_day is not None and settle_day.day != row.settled_on
        if settle_day is not None:
            record_settle_day(row, settle_day)
        elif new_status_id not in settled_status_ids():
            record_settle_day(row, None)
            released = True
        elif row.settled_on is None:
            # The band's first entry with no day stated.  ``entered`` is the
            # basis because nothing outside the app said anything: the owner
            # marked the row paid and the door supplied their today.  It is the
            # honest answer rather than a weaker fourth member -- what separates
            # it from ``observed`` is provenance, and a day the owner stands
            # behind is a point exactly as a day the bank states is.
            record_settle_day(
                row,
                SettleDay(
                    day=display_today(),
                    basis=SettledDayBasisEnum.ENTERED,
                ),
            )
        if released:
            row.reconciled_by_id = None
        # **WHAT MOVED IS RETAINED WHEN THE ASSERTION IS RELEASED**, and that
        # asymmetry with the two lines above is the whole point (plan step
        # X-au-c3).  ``settled_on``, ``settled_day_basis_id`` and
        # ``reconciled_by_id`` are the ASSERTION -- "this money moved, on this
        # day, that is what kind of day it is, and that statement showed it" --
        # so a revert withdraws all three.  What moved is a fact about the row
        # and not about the assertion, and its home is the COVERING MOVEMENT
        # written below: a revert un-dates that movement and keeps it (plan
        # step X-bi-3e-2, ruling **R-BAL61**), so nothing here destroys it.
        #
        # A first version of this step released everything together, under a
        # CHECK that paired the day with the record.  That pairing was the same
        # defect the step exists to remove (finding **N-241**: one column
        # answering two questions) rebuilt as two facts forced to share one
        # lifetime -- and it cost the user real data, because the full-edit
        # popover TELLS them to revert in order to edit, so following the app's
        # own instruction silently deleted a figure they had read off a
        # statement.  Every reconciliation system this was checked against
        # separates the two: an amount belongs to the transaction and
        # cleared-ness is metadata over it, so un-clearing never touches the
        # amount (developer, 2026-08-17).
        #
        # Nothing is lost by retaining it and one thing is gained: a re-settle
        # HONOURS a retained correction (:meth:`Settlement.from_settle`), so the
        # revert / edit / re-settle round trip the popover describes is lossless.
        # What keeps a retained figure out of every balance is not its absence
        # but the STATUS -- ``row_valuation.settled_figure`` answers ``None`` for
        # a row that is not settled, whatever it still carries.
        #
        # The row's own ``settled_amount`` / ``settled_basis_id`` were written
        # here beside the movement from X-au-c3 until plan step
        # ``balance:X-bi-4b-2`` deleted them (ruling **R-BAL80**, migration
        # ``45f10b870c8b``): the same fact in two homes is two sources.
        #
        # **The record's home** (plan step **X-bi-3a**, ruling **R-BAL39**):
        # a settle on the manual branch is recorded as ONE covering movement,
        # the payment row that records a bill's money the way a purchase
        # records an envelope's, and leaving the band un-dates it and keeps
        # it (plan step X-bi-3e-2, ruling R-BAL61: the movement follows the
        # row's released assertion and outlives it).  Written LAST so every
        # value it carries -- the day pair, the link, the figure and its
        # source -- is the row's final one for this act.  The rule and the
        # lifecycle are
        # :mod:`app.services.status_seam._covering`'s; it covers every kind
        # of row (expense since 3a, income since 3b), and a transfer's sides
        # are the Transfer arm's (``_side``) since plan step
        # ``balance:X-bi-6-4d-2``.
        sync_covering_movement(
            row,
            was_settled=was_settled,
            now_settled=new_status_id in settled_status_ids(),
            settlement=settlement,
            press=press,
        )

    db.session.expire(row, ["status"])
