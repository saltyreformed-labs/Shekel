"""
Shekel Budget App -- Account Books-Opening Service

**The ONE writer of ``budget.account_openings``** (plan step **X-f3c-2b-2a**),
and the service tier of the rule :mod:`app.opening_infrastructure` states at
the database tier.  Two EVENTS reach it: an account's ORIGINATION
(``account_service.create_account``) and an owner RESTATING what their books
opened with.

**Why one writer, and it is ruling R-ES applied one table over.**  The
assertion table used to have two writers -- the account factory constructing
the row itself, and :func:`app.services.anchor_service.stage_anchor_true_up` --
and the two differed in every rule that is not the row's columns: the stager
took the owner's write lock, applied ruling **R-EQ**'s did-this-change compare
and logged the resolved day; the factory did none of it.  Routing both events
through one door is what makes those rules properties of the TABLE rather than
of whichever function happened to do the INSERT (the lock was one of them until
plan step ``balance:X-bn`` made it the transaction's).  This module is that
door for the third member of the append-only account family.

**What the owner is stating, in one sentence.**  An account's opening equity is
the capital its books opened with -- the level every balance the app has ever
rendered for it is stacked on top of.  Until this step it was written once, at
creation, and could never be corrected: findings **N-275** and **N-379** each
measure a production figure WRONG against the owner's own bank (``$436.05`` on
Checking; the whole of the Fidelity history), and the X-f3c-2a migration
derived seven of the nine that stand.  A figure a surface flags as a guess
(``migration_derived``) with no way to replace it is a defect, not a caption.

**Every restatement is an APPEND and the latest governs**, so the record of
what the books used to say survives the correction
(:class:`~app.models.account_opening.AccountOpening`).  An UPDATE is refused at
three tiers -- the ORM listener, ``budget.refuse_append_only_change`` and the
``BEFORE TRUNCATE`` arm beside it (rulings **R-HY**, **R-IC**) -- so "restate"
can only ever mean "say a new thing", never "erase the old one".

**Two rules bound the day, and the one that is NOT here is the point.**

* The books may not open on or after a day the account already records money
  moving (:func:`app.services.cash_ledger.reject_books_open_on_or_after_movements`,
  ruling **R-HG**).  That is the boundary this arc exists to hold, and the
  database holds it structurally; the service asks it so a date box gets a
  sentence rather than a database driver's abort at COMMIT.
* The books may not open on a day that has not happened
  (:func:`_reject_future_opening`).  An opening equity is what the account held
  at the CLOSE of its day, and nobody has seen the close of tomorrow.

``pay_period_service.earliest_recordable_day`` is deliberately **not** asked,
and the plan says so in as many words: that floor is a rule about ASSERTIONS
(ruling **R-ER**) -- it exists because an assertion opens the modelled-return
window and seeds contribution history -- and applying it here would make an
account's books unopenable before the owner's calendar begins.  Which is the
common case for a real account: the bank opened it years before Shekel knew
about it.  The opening's journal entry needs no period of its own either;
:meth:`app.services.pay_calendar.PayCalendar.filing_period` CLAMPS a
pre-calendar day onto the earliest period rather than refusing it.

This service is Flask-isolated per the project architecture rule: plain data
in, a plain discriminant out, no ``request`` / ``session`` import.
:func:`stage_account_opening` flushes nothing and commits nothing (its caller
owns the transaction); :func:`apply_opening_restatement` owns its own, exactly
as :func:`app.services.anchor_service.apply_anchor_true_up` owns its own beside
:func:`~app.services.anchor_service.stage_anchor_true_up`.
"""

import enum
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AccountOpeningSourceEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.account_opening import AccountOpening
from app.services import account_posting_service, cash_ledger, planned_rows_books
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceGenerationError, RecurrenceResolutionError
from app.utils.dates import display_today


logger = logging.getLogger(__name__)


class AmortizingAccountOpeningError(ValueError):
    """Raised when a books restatement targets an amortizing loan account.

    A CONFIGURED loan's opening is :attr:`~app.models.loan_params.LoanParams.original_principal`,
    materialised by ``loan_loaders.synthesize_origination_anchor``, and its
    balance is the amortization replay
    (:func:`app.services.balance_at.balance_at` dispatches on
    ``_resolution.configured_loan``).  Such an account carries a
    ``budget.account_openings`` row -- every account does, because the fold
    falls through to it for an amortizing account with no
    :class:`~app.models.loan_params.LoanParams` -- and **nothing reads it while
    the loan is configured**: its balance starts from its terms, and its books
    start on its origination (card plan#2436, ruling **plan#3954**), the
    answer every reader of a books start asks the one producer for
    (:func:`app.services.cash_ledger.books_start`;
    ``budget.account_books_opened_on`` in SQL).  A door that wrote there would
    report success and move no figure, which is the silent no-op this arc
    exists to delete.

    The twin of :class:`~app.services.anchor_service.AmortizingAccountAnchorError`,
    and refused for the same reason at the same layer: a loan's balance is
    ledger-derived and is corrected on the loan's own page.

    **The limitation this accepts, and its first statement here was WRONG.**
    An amortizing account whose loan params are not filled in yet DOES have its
    opening read by the cash fold -- ``_resolution.configured_loan`` answers
    ``None``, so ``balance_at`` falls through -- and this refuses to restate
    it.  That was justified on the claim that such a figure is
    ``user_declared`` from creation, which an adversarial review measured false
    (2026-08-31): migration ``a7c41f9d2b60`` stamps ``migration_derived`` on
    EVERY account that predated the table, the two production loans included,
    and the Van Loan's derived opening is the ``-$531.94`` this repo quotes in
    three places.  So the honest limitation is larger than the one first
    written: on a params-less amortizing account the opening is read by the
    fold, is rendered nowhere, and cannot be corrected here.  It is accepted
    because the state is a half-built account whose repair is to finish the
    loan-params form -- at which point nothing reads the row at all -- and
    because widening the refusal's predicate from the account KIND to
    ``configured_loan`` would put a private ``balance_at`` submodule's question
    in a service that must not import one.
    """


class OpeningRestatementOutcome(enum.Enum):
    """Discriminant returned by :func:`apply_opening_restatement`.

    The route picks its flash message from this; the service never touches the
    response layer.  The same two-member shape
    :class:`~app.services.anchor_service.AnchorTrueUpOutcome` has, and for the
    same reason -- ruling **R-EQ**: a submission that changes nothing is not an
    error, it is the state the caller asked for already standing.

    Members:
        COMMITTED: A new :class:`~app.models.account_opening.AccountOpening`
            row was appended, the account's posted anchor corrections were
            re-based onto it, and the commit succeeded.
        UNCHANGED: The submission matched the opening that already GOVERNS, so
            nothing was written and the session was rolled back.
    """

    COMMITTED = "committed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class BooksOpening:
    """What an account's books opened with: a civil day and the capital.

    Two values that are ONE fact -- "this account's books opened on day D
    holding $E" -- so the door takes them together rather than as a widening
    keyword list, and so a caller cannot supply one without the other.  The
    same argument :class:`app.routes.accounts.anchor._AnchorSubmission` makes
    for the assertion pair one table over.

    **It is NOT a bounded-day type, and that is deliberate**, because the two
    events that reach this module bound their day DIFFERENTLY and a shared type
    saying "already bounded" would be lying about one of them.  An origination
    takes its day from the assertion it is created beside, already bounded by
    :func:`app.services.anchor_service.resolve_observation_day`; a restatement
    is bounded by the account's recorded movements and by today
    (:func:`_reject_restatement_day`).  What makes an unbounded day
    UNSTORABLE, for both events and for every writer nobody enumerated, is the
    deferrable constraint trigger :mod:`app.opening_infrastructure` installs --
    not a Python type.

    Attributes:
        opened_on: The civil day the books opened, in the owner's timezone.
            The equity is the balance at its CLOSE (ruling **R-HG**), which is
            why no movement may be dated on or before it.
        equity: The capital the books opened with, LEDGER-NATIVE and signed
            exactly as an assertion's ``anchor_balance`` is -- positive for an
            asset's opening capital, negative for a liability's.  ``Decimal``
            money, never ``float``.
    """

    opened_on: date
    equity: Decimal


def _reject_future_opening(opened_on: date) -> None:
    """Refuse books opened on a day that has not happened.

    An opening equity is what the account held at the CLOSE of *opened_on*, so
    a future day states a fact about a close nobody has seen -- and it would
    seed the fold's running total at a level the account is only projected to
    reach.  Both existing writers of a day on this family already refuse it
    (:func:`app.services.anchor_service.resolve_observation_day` for an
    assertion, and therefore for the origination opening beside it), so
    allowing it here would let a RESTATEMENT reach a state CREATION cannot.

    **The clock is the USER's** (ruling R-DH (b)).  ``display_today()``, never
    ``date.today()``: the process's UTC day is already tomorrow at 8pm Eastern,
    so the server's clock would refuse a restatement the owner is making right
    now.

    Args:
        opened_on: The candidate opening day.

    Raises:
        ValidationError: When *opened_on* is after the owner's today.
    """
    today = display_today()
    if opened_on <= today:
        return
    raise ValidationError(
        f"These books cannot open on {opened_on.isoformat()}: that day has "
        f"not happened yet (today is {today.isoformat()}).  An opening "
        "equity is what the account held at the END of the day it names."
    )


def _reject_restatement_day(
    account_id: int, user_id: int, opened_on: date,
) -> None:
    """Apply all FIVE of a RESTATEMENT's day rules, in the order that reads best.

    The future rule first and the four record rules after it, because a day
    that has not happened is wrong about itself before it is wrong about the
    records: telling an owner who typed next year that "this account already
    records money moving on 2026-03-27" answers a question they did not ask.

    **The ASSERTION rule was missing until a code review found what that cost**
    (2026-08-31).  The movement rule bounds an opening against recorded cash,
    and an account with no settled movement -- every investment, retirement and
    property account in production -- had no bound at all below today.
    Reproduced on the developer's Roth IRA: ``$22,809.02`` of investment return
    fabricated, and the stated opening silently discarded.

    **The MATCHED-LINE rule was missing for a sharper reason** (plan step
    **balance:X-f3c-2b-2b**): it is a gap INSIDE the movement rule rather than
    beside it.  A match settles every member on the LATEST of its bank days, so
    the earliest line of a multi-day group posts strictly before the row
    explaining it settles -- and every day in between passes the movement bound
    while putting that line's money inside the new opening equity and inside a
    settled row at once.  It is reachable rather than instantiated: measured
    2026-08-31, account 1's earliest matched line and earliest movement are the
    same day, and production holds no match at all.

    **The PLANNED-ROW rule is ruling R-PC88** (plan step ``pay_calendar:C18-a``,
    :func:`_reject_books_open_on_or_after_planned_rows`), and it is last
    because its repair is the cheapest: mark the row paid or cancel it.  It
    exists because ruling **R-PC85** made a recurring definition's
    occurrences stop at its accounts' books, so a restatement moving the books
    past a still-projected recurring row would leave that row answering an
    occurrence the walk drops -- and a maintain pass that reaches its
    paycheck retires such a row (plan step R10-a), without a word.

    Stated as one function rather than five calls at the door so the ORDER is a
    property of the rule set rather than of whichever caller ran first -- the
    shape :func:`app.services.anchor_service.resolve_observation_day` gives the
    assertion door's two bounds.

    Args:
        account_id: The account whose books are being restated.  Assumed
            already scoped to the acting user by the caller.
        user_id: The account's owner (the planned-row rule reads their pay
            calendar).
        opened_on: The candidate opening day.

    Raises:
        ValidationError: When the day is in the future, on or after a day the
            account already records money moving, on or after a day it has
            matched a bank line on, after a day it has asserted a balance
            for, or where it would strand a still-projected recurring row
            of a definition that moves money in it -- or where no walk can
            tell, because such a definition's stored rule cannot be read.
    """
    _reject_future_opening(opened_on)
    cash_ledger.reject_books_open_on_or_after_movements(account_id, opened_on)
    # **The MATCHED-LINE bound, and it is not implied by the movement one
    # above** (plan step **balance:X-f3c-2b-2b**).  A match settles every
    # member on the LATEST of its bank days, so a group's earliest line posts
    # strictly before the row explaining it settles -- and every day in that
    # window passes the movement bound while putting the line's money inside
    # both the new opening equity and a settled row.  Asked second because the
    # repairs differ in cost: a movement is the owner's own record and can be
    # re-dated, while undoing a match discards work.
    cash_ledger.reject_books_open_on_or_after_matched_lines(
        account_id, opened_on,
    )
    cash_ledger.reject_books_open_after_an_assertion(account_id, opened_on)
    _reject_books_open_on_or_after_planned_rows(account_id, user_id, opened_on)


def _reject_books_open_on_or_after_planned_rows(
    account_id: int, user_id: int, opened_on: date,
) -> None:
    """Refuse books opening where a still-projected recurring row would be stranded.

    **Ruling R-PC88** (developer, 2026-09-22; plan step ``pay_calendar:C18-a``).
    A recurring definition's occurrences start above the books of every
    account it moves money in (ruling **R-PC85**).  Moving the books LATER
    past a row that is still Projected -- an unpaid, overdue bill the rule
    generated -- makes the walk drop the occurrence it answers, and a
    maintain pass that reaches such a row retires it, silently raising the
    forecast.  The owner decides instead: marked paid it becomes a movement
    (and the movement rule speaks), cancelled it holds nothing (a generated
    row's day is its schedule's, so no move could clear it; the C18-a
    review's M1).  An ARCHIVED definition's hidden rows count, since its
    unarchive would bring them back (ruling **R-PC93**); the refusal names
    such a row as the archived definition's, with the remedy that reaches it.

    **Which rows, which day, and the comparison are one producer's**
    (:func:`app.services.planned_rows_books.first_row_an_opening_strands`,
    which the books-opening card's date ceiling reads too), asking two
    questions (ruling **R-PC99**).  Every recurring definition moving money
    in the account is walked with the books it would have if the account
    opened on *opened_on*, and a still-Projected row answering an occurrence
    that walk drops -- live, or hidden by an archive, wherever it sits -- is
    stranded, named by the day the walk compares for it, a bill's due day or
    an envelope's paycheck's last day (rulings **R-PC86**, **R-PC89**), under
    the strict :func:`~app.utils.books_boundary.books_hold`: the walk
    re-dates a row by its rule and bounds it by its DEFINITION's accounts
    (the step's adversarial review, H1).  And every still-Projected recurring
    row SITTING ON the account whose own stored day the candidate books hold
    is stranded too, whatever account its definition names now (rulings
    **R-PC98** and **R-PC99**): the balance counts it here, and a definition
    that moved off the account left the rows of paychecks that had already
    ended on it.  The sentence says what the move does to the row it names:
    sits inside the opening, or -- a row its schedule drops while it sits on
    another account -- is deleted by the next pass to reach it (a hidden one,
    which no pass reaches, is named without that clause).

    No figure is named: the refusal is about a DAY, so the opening door's
    HELD-figure contract (ruling **R-CC52**) is untouched.

    **A stored rule the walk cannot read REFUSES the restatement** (the
    step's adversarial review, L7).  With no walk there is no telling whether
    the new books strand a row, so the door fails closed -- the answer
    :func:`~app.services.planned_rows_books.reject_revert_below_the_books`
    gives the same state (ruling **R-PC97**) -- where the recurrence error
    used to reach the owner as a 500.  No door writes such a rule, so the log
    carries the error for the repair.  The sentence names no item: the
    producer walks every definition moving money in the account and its error
    carries no definition.

    Args:
        account_id: The account whose books are being restated.
        user_id: Its owner, whose pay calendar every walk places on.
        opened_on: The candidate opening day.

    Raises:
        ValidationError: When books opening on *opened_on* would strand a
            still-projected recurring row, live or hidden by an archive, or
            when a recurring definition moving money in the account has a
            stored rule the walk cannot read.
    """
    try:
        stranded = planned_rows_books.first_row_an_opening_strands(
            account_id, opened_on, calendar_for(user_id),
        )
    except (RecurrenceResolutionError, RecurrenceGenerationError) as exc:
        logger.warning(
            "Refusing to restate account %d's books: a recurring definition "
            "moving money in it has a stored rule that cannot be walked.",
            account_id, exc_info=True,
        )
        raise ValidationError(
            f"These books cannot open on {opened_on.isoformat()} while a "
            "recurring item moving money in this account has a schedule that "
            "cannot be read: repair its schedule first."
        ) from exc
    if stranded is None:
        return
    raise ValidationError(
        f"These books cannot open on {opened_on.isoformat()}: the recurring "
        f"{stranded.described()}.  {stranded.consequence()}  "
        f"{stranded.remedy()}, then restate the books."
    )


def stage_account_opening(
    *,
    account: Account,
    opening: BooksOpening,
    source: AccountOpeningSourceEnum,
) -> bool:
    """Append *account*'s books opening without committing.

    **The ONE writer of ``budget.account_openings``.**  Its two callers are the
    ``apply`` wrapper below (an owner restating the books) and
    :func:`app.services.account_service.create_account` (the origination), so
    ruling **R-EQ**'s did-this-change compare and the audit line are properties
    of the TABLE rather than of one of the two events (the owner's write lock
    is the transaction's where it holds one, plan step ``balance:X-bn``; see
    the lock paragraph below).  Adds to the current session; the caller
    commits.

    **It decides whether there is anything to append, and that decision is
    ruling R-EQ** -- the same rule
    :func:`~app.services.anchor_service.stage_anchor_true_up` applies to an
    assertion.  A restatement that names the day and the figure already in
    force is not an error and is not a second fact: it is what already stands.
    Appending it anyway would grow the restatement history by a row saying
    nothing, on a table whose whole purpose is that every row is a statement
    somebody made.

    **The comparison is against the GOVERNING row, and for this table that is
    the LATEST row rather than the latest one on or before some horizon.**  The
    assertion door has to ask its question as of the submitted DAY, because an
    assertion is true of its own day and a back-dated one governs only from
    then (finding: comparing against the account's newest row instead makes
    every back-dated double-click append).  An opening is not like that: an
    account has exactly ONE opening at a time, the newest recording of it, so
    there is one row to compare against whatever day is being proposed.

    **The lock precedes the read**, exactly as it does one table over: a
    compare-then-append is a read-modify-write, and an unserialised one lets
    two concurrent submissions each read the pre-state and both append.  On a
    signed-in request the owner's write lock is already held when this runs:
    every command transaction such a request opens takes it before reading
    any of the owner's data (plan step ``balance:X-bn``,
    :mod:`app.db_transaction`), so before this read.  **Registration takes
    none** -- ``registration_service.register_user`` reaches this function
    through the account factory for a user who is not committed yet, so no
    other transaction can see the account -- and a script that writes an
    existing owner's books must take that owner's lock at its own start.
    *Until that step the lock was taken HERE, with the read it protects, and
    ruling R-CC115 deleted the acquisition.*

    **It does NOT bound the day, and the caller must have done so.**  See the
    module docstring: the two events bound it differently, and a second
    application of a CLOCK-dependent rule is the defect ruling **R-ER** deletes
    one door over.  What makes an unbounded day unstorable is the deferrable
    constraint trigger over this table, not this function.

    Args:
        account: An attached :class:`~app.models.account.Account` row, already
            flushed so ``account.id`` is set.  Caller owns the ownership check.
        opening: The :class:`BooksOpening` to record -- the day and the
            capital, already bounded by the caller.
        source: Where the figure came from
            (:class:`~app.enums.AccountOpeningSourceEnum`).  **Required with no
            default**, because its two values are *a human stated this* and
            *the app computed it*, and a default would let a future writer
            claim an observation by omission -- the same reason
            ``statement_match._create.create_purchase_from_line`` gives its own
            ``applied_by_rule`` flag neither a default nor a positional slot.
            A reader that cannot tell the two apart presents a guess and a fact
            identically, which is the defect finding **N-275** is made of.

    Returns:
        ``True`` when a row was appended to the session; ``False`` when the
        submission matched the governing opening and nothing was staged.  The
        caller decides what unchanged means for ITS transaction.
    """
    source_id = ref_cache.account_opening_source_id(source)
    governing = cash_ledger.governing_account_opening(account.id)
    if governing is not None and (
        (governing.opened_on, governing.opening_equity, governing.source_id)
        == (opening.opened_on, opening.equity, source_id)
    ):
        return False

    db.session.add(AccountOpening(
        account_id=account.id,
        opened_on=opening.opened_on,
        opening_equity=opening.equity,
        source_id=source_id,
    ))
    # Both events reach this line, so the audit trail is uniform whichever one
    # wrote the row -- an origination or a restatement -- and the PROVENANCE is
    # logged beside the figure, because "who said this" is the fact that
    # separates a corrected opening from the guess it replaced.
    logger.info(
        "Books opening staged: account %d opens %s at $%s (%s)",
        account.id, opening.opened_on.isoformat(), opening.equity, source.value,
    )
    return True


def apply_opening_restatement(
    *, account: Account, opening: BooksOpening,
) -> OpeningRestatementOutcome:
    """Restate what *account*'s books opened with, re-base its postings, commit.

    The owner-facing door (plan step **X-f3c-2b-2a**): bounds the day, appends
    the restatement through :func:`stage_account_opening`, re-bases the
    account's posted anchor corrections onto it, and commits.  Returns an
    :class:`OpeningRestatementOutcome` the caller translates into its rendered
    response.

    **The provenance is ``user_declared`` and this door cannot say otherwise.**
    Only the X-f3c-2a migration writes ``migration_derived``, and it is frozen
    history -- the pre-X-f3c-2a inference, kept legible precisely so a surface
    can tell a guess from an observation.  A door that let an owner mark their
    own figure "derived" would erase the distinction the column exists for.

    **The posting re-sync is not optional and it moves more than the amount.**
    ``budget.account_openings`` is read by BOTH the balance fold and the posted
    ledger (ruling **R-GX**): ``ledger_lines.walk_account_ledger``
    books the ``account_opening`` journal entry dated on ``opened_on`` and
    seeds every later correction's ``ledger_before`` from the equity.  Moving
    either therefore re-keys that entry -- ``(source kind, pay period, entry
    date)`` -- and changes the delta of every true-up after it.  The reconcile
    walks the UNION of the target and posted keys
    (:func:`app.services._posting_reconcile.emit_correction_deltas`), so the
    old-dated entry is reversed to zero and the new one posted in the same
    transaction; nothing is orphaned and nothing needs a backfill.

    **Restating the opening ALONE does not settle the account's books, and the
    step that ships this door measured why.**  The account's later assertions
    still say what they said, so the gap the restatement opens is booked as a
    correction against them rather than absorbed: on the developer's own
    archived Fidelity account, taking a ``$4,863.56`` opening to ``$0.00``
    leaves the 2026-04-06 assertion booking a ``$5,363.56`` true-up and the
    asset returns in full. Correcting an assertion is the OTHER door's act
    (:func:`app.services.anchor_service.apply_anchor_true_up`), deliberately:
    an opening and a balance reading are two different facts, and one door
    doing both is how the app came to have two definitions of the opening in
    the first place.

    **It touches no MOVEMENT, by construction rather than by care.**  The
    constraint refuses a restatement onto or past a recorded movement, so a
    legal restatement is one every movement already sits after; there is
    nothing for it to re-date and nothing to reconcile.

    Args:
        account: An attached :class:`~app.models.account.Account`.  Caller owns
            the ownership check (routes 404 for cross-owner access).
        opening: The :class:`BooksOpening` submitted -- the day and the
            capital.  Bounded HERE rather than by the caller, so every entrance
            to this door shares one rule; the route adds no bound of its own
            and must not, or two entrances would answer one submission
            differently.

    Returns:
        The :class:`OpeningRestatementOutcome`.  ``UNCHANGED`` when the
        submission matched the opening that already governs, in which case this
        function has rolled the session back and written nothing.

    Raises:
        AmortizingAccountOpeningError: When ``account`` is an amortizing loan.
            Raised BEFORE anything is staged, so the session is clean.
        ValidationError: When the day breaks one of the five day rules
            (:func:`_reject_restatement_day`): in the future, on or after a
            recorded movement or a matched bank line, after an assertion, or
            where it would strand a still-projected recurring row (ruling
            **R-PC88**) -- or where no walk can tell, because a recurring
            definition's stored rule cannot be read.  Raised before anything
            is staged.
    """
    acct_type = account.account_type
    if acct_type is not None and acct_type.has_amortization:
        raise AmortizingAccountOpeningError(
            f"account {account.id} is an amortizing loan; its opening is "
            "LoanParams.original_principal and nothing reads a books opening "
            "row while the loan is configured"
        )

    # The owner's write lock is already held: the signed-in request's
    # transaction took it before reading any of the owner's data (plan step
    # ``balance:X-bn``, :mod:`app.db_transaction`), so the day rule below
    # reads ``budget.transactions`` / ``budget.transaction_entries`` after any
    # concurrent settle of this owner has committed: the loser sees the
    # committed movement and renders the 400 rather than meeting the DEFERRED
    # trigger's raw error at COMMIT.  *Until that step this door took the lock
    # here, before the day is judged, deliberately earlier than the sibling
    # anchor door took its own; ruling R-CC115 deleted both acquisitions.*
    # The kind gate ran FIRST, above, so an amortizing account is refused for
    # what it IS before its day is judged.
    _reject_restatement_day(account.id, account.user_id, opening.opened_on)

    if not stage_account_opening(
        account=account,
        opening=opening,
        source=AccountOpeningSourceEnum.USER_DECLARED,
    ):
        # Ruling R-EQ: the submission IS the governing opening, so there is
        # nothing to append and nothing for the reconcile to move.  Roll back
        # rather than returning on an open transaction -- the transaction holds
        # the owner's write lock (taken before it read any of the owner's data,
        # plan step ``balance:X-bn``), and only a commit or a rollback releases
        # it.  Read the id BEFORE the rollback: afterwards the instance is
        # expired and touching an attribute opens a fresh transaction purely to
        # recover a value already in hand.
        account_id = account.id
        db.session.rollback()
        logger.info(
            "Books restatement for account %d states the opening that already "
            "stands; nothing written (idempotent success)",
            account_id,
        )
        return OpeningRestatementOutcome.UNCHANGED

    # EVERY scenario, because a books opening is per-ACCOUNT and scenario-free
    # (ruling **R-GX**, defect 2 of four): the quantity is what existed before
    # tracking began, and money that existed before tracking began cannot be a
    # function of a what-if.  The fresh row autoflushes into the walk's first
    # query, so the reconcile re-keys the opening entry in this transaction.
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )
    db.session.commit()
    return OpeningRestatementOutcome.COMMITTED
