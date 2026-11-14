"""
Shekel Budget App -- Books-opening restatement route

The door an owner corrects an account's OPENING EQUITY through (plan step
**X-f3c-2b-2a**): the card the shared account edit page renders, and the POST
that appends the restatement.

**Why it is not in :mod:`app.routes.accounts.crud`.**  That module owns account
CRUD -- a name, a type, a sort order, an active flag, all plain columns on one
row -- and this writes an append-only MONEY fact through a service that
re-bases the posted ledger under the owner's write lock.  It is a third subject
beside it exactly as :mod:`app.routes.accounts.history` is a third subject
beside ``detail``'s page and ``anchor``'s write door, and it is split for the
same reason: a door with its own service, its own schema and its own refusals
does not belong inside a route module that has none of them.  ``crud`` also
stood at 761 of pylint's 1000-line ceiling before this step.

**The card is rendered by the EDIT page and linked to by the balance-history
card, which is one door with two entrances rather than two doors** (developer
ruling, 2026-08-31).  The opening is DISPLAYED in exactly one place -- the
``<tfoot>`` of ``accounts/_balance_history.html``, on the cash detail page --
and that page serves three of the owner's nine accounts.  Four of the other six
(the two IRAs, the 401(k) and the Property) carry a ``migration_derived``
opening the balance fold READS, so a door reachable only from the cash card
would leave the accounts most likely to hold a wrong figure with no way to
correct it.  The shared edit form is the one surface every account kind reaches
(the cockpit card's kebab -> Edit), so the form lives there and the card points
at it.

**A full page rather than an inline editor, and that is a design decision
rather than a shortcut.**  Asserting a balance is the one-click habit this app
is built around -- five surfaces open that editor and it defaults its own date
box to today.  Restating what the books opened with is the opposite: rare,
consequential, and the one act that moves the level every balance the account
has ever rendered is stacked on.  ``app.opening_infrastructure`` calls a
restatement rare in as many words.  A deliberate page with the standing figures
pre-filled is the honest affordance for it, and it also keeps the
balance-history card what its own module says it is -- a RECORD, not a write
door.

Services boundary: this module owns the HTTP-shaped concerns (the ownership
check, the kind gate, schema validation, the flash and the redirect) and every
refusal that is about money or dates belongs to
:mod:`app.services.opening_service`.  No arithmetic happens here or in the
template.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from flask import flash, redirect, request, url_for
from flask.typing import ResponseReturnValue

from app.enums import AccountOpeningSourceEnum
from app.exceptions import ValidationError
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._door_meaning import door_meaning_refusal
from app.services import cash_ledger, liability_sign, opening_service
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.opening_service import OpeningRestatementOutcome
from app.services.pay_calendar import calendar_for
from app.services.planned_rows_books import StrandedRow, first_row_an_opening_strands
from app.services.recurrence import RecurrenceGenerationError, RecurrenceResolutionError
from app.utils.account_validation import _opening_schema
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.utils.error_fragments import flatten_schema_errors
from app import ref_cache

logger = logging.getLogger(__name__)


#: The refusal an amortizing account's restatement gets, shared by the card
#: (which is not rendered at all) and the POST (which answers it).  A loan's
#: opening is ``LoanParams.original_principal`` and nothing reads its
#: ``budget.account_openings`` row while the loan is configured: its books
#: start on its origination, the answer every reader of a books start asks
#: ``cash_ledger.books_start`` for (card plan#2436).  So a door here would
#: report success and move no figure -- the twin of
#: ``_door_meaning.LOAN_ANCHOR_REFUSAL`` one fact over.
LOAN_OPENING_REFUSAL = (
    "A loan's opening is its original principal, recorded with the loan's "
    "terms. Correct it on the loan's own page."
)


@dataclass(frozen=True)
class OpeningCeiling:
    """The latest day an account's books may open, and WHICH bound says so.

    Attributes:
        day: The date input's ``max``.
        said: The help text under it, naming the bound that is actually
            binding.
    """

    day: date
    said: str


def _opening_ceilings(
    earliest_movement: "date | None",
    earliest_assertion: "date | None",
    earliest_matched_line: "date | None",
    first_planned_row: StrandedRow | None,
) -> "list[OpeningCeiling]":
    """Return every bound on this account's opening day, in REFUSAL order.

    **The order is** :func:`~app.services.opening_service
    ._reject_restatement_day`'s **own**, and that is what makes the sentence
    the card prints the sentence the door would give.  Ties are ordinary --
    ``account_service.create_account`` writes the origination opening and its
    assertion on one day -- and :func:`min` keeps the FIRST minimum, so the
    bound named here is the first one the service would raise on for a day one
    past the ceiling.

    Args:
        earliest_movement: The account's earliest recorded movement day, or
            ``None`` where it records none.
        earliest_assertion: The earliest day the owner has asserted a balance
            for, or ``None``.
        earliest_matched_line: The earliest day the account has matched a bank
            line on, or ``None``.
        first_planned_row: The earliest still-projected recurring row books
            opening TODAY would strand (ruling **R-PC88**; the producer the
            door's own refusal asks,
            :func:`~app.services.planned_rows_books.first_row_an_opening_strands`),
            or ``None``.  Asked at today because that is the latest day the
            box takes, and a later opening strands every row an earlier one
            does: so the day before this row's compared day is the latest that
            strands none of the rows only THIS account's books bound.  A row
            a definition's OTHER account already strands is refused at any
            day, which no ceiling can express; the door's refusal says so.

    Returns:
        One :class:`OpeningCeiling` per bound that applies, always non-empty --
        the clock bounds every account.
    """
    ceilings = [OpeningCeiling(
        day=display_today(),
        # **It says why TODAY bounds the box, and asserts nothing about the
        # account's records.**  It was "This account records nothing yet, so
        # any past day will do" -- the template's old ``{% else %}`` arm,
        # which only ran when no other bound existed.  Here it is the FIRST
        # candidate and ``min`` keeps the first minimum, so it wins every tie
        # -- and ``account_service.create_account`` writes the origination
        # opening and its assertion on ONE day, so a brand-new account ties at
        # today and would have been told it records nothing while holding an
        # asserted balance.  Found by adversarial design review 2026-08-31.
        said=(
            "The books cannot open on a day that has not happened yet, so "
            "today is the latest day this box will take."
        ),
    )]
    if earliest_movement is not None:
        ceilings.append(OpeningCeiling(
            # MINUS a day: the service refuses a movement dated ON the opening
            # as well as before it, so the last legal day is the one before.
            day=earliest_movement - timedelta(days=1),
            said=(
                "This account already records money moving on "
                f"{earliest_movement.strftime('%b %-d, %Y')}, so the books "
                "have to open before that day."
            ),
        ))
    if earliest_matched_line is not None:
        ceilings.append(OpeningCeiling(
            # MINUS a day, with the movement term: a bank line dated ON the
            # opening day is inside the opening equity.
            day=earliest_matched_line - timedelta(days=1),
            said=(
                "You have matched a bank line your bank posted on "
                f"{earliest_matched_line.strftime('%b %-d, %Y')}, so the "
                "books have to open before that day -- undo that match first "
                "if your records really do start earlier."
            ),
        ))
    if earliest_assertion is not None:
        ceilings.append(OpeningCeiling(
            # NOT minus a day: an opening EQUAL to the earliest assertion is
            # what every account is created holding, so the last legal day is
            # that day itself.
            day=earliest_assertion,
            said=(
                "You have already recorded a balance for this account on "
                f"{earliest_assertion.strftime('%b %-d, %Y')}, so the books "
                "have to open on or before that day -- the app folds forward "
                "from the balances you record."
            ),
        ))
    if first_planned_row is not None:
        ceilings.append(OpeningCeiling(
            # MINUS a day: the row's compared day ON the opening is inside it.
            day=first_planned_row.books_day - timedelta(days=1),
            said=(
                f"The recurring {first_planned_row.described()}, so the "
                "books have to open before that day.  "
                f"{first_planned_row.remedy()} if your records really do "
                "start later."
            ),
        ))
    return ceilings


def _latest_legal_opening_day(
    earliest_movement: "date | None",
    earliest_assertion: "date | None",
    earliest_matched_line: "date | None",
    first_planned_row: StrandedRow | None,
) -> OpeningCeiling:
    """Return the latest day this account's books may legally open on.

    The date input's ``max``, so the browser refuses what
    :mod:`app.services.opening_service` would refuse rather than round-tripping
    a rejection.  Every bound comes from the same PRIMITIVES the service
    refuses by, never from a template literal: the owner's today
    (``display_today()``, ruling **R-DH (b)** -- the process clock is pinned to
    the display zone in the deployed container but not in CI or a script), the
    account's earliest recorded movement, its earliest MATCHED bank line, its
    earliest asserted balance and the first still-projected recurring row its
    books would strand (ruling **R-PC88**, plan step ``pay_calendar:C18-a``:
    the fifth rule, which the card offered days past until that step's
    adversarial review, M2).

    **The layering is deliberate, not redundant**, and it is the argument
    ``anchor._anchor_day_bounds`` makes for its own pair: an input bound is
    captured at RENDER time, and every one of these moves -- the clock at
    midnight, the movement floor whenever a row is settled or re-dated -- so a
    form left open across such a change can still submit a day the service
    refuses.  That is why the refusal is also rendered rather than assumed
    unreachable.

    **It returns the SENTENCE beside the day, and that is a defect fix rather
    than a convenience** (plan step **balance:X-f3c-2b-2b**).  The card picked
    between the bounds itself, with ``{% if %}``/``{% elif %}`` in the order
    movement-then-assertion -- which is not the order ``min`` resolves them in.
    An account asserted in January and first settled in June has its ceiling
    set by the ASSERTION and was told "this account already records money
    moving on Jun 1, so the books have to open before that day": a true
    sentence about a bound that was not the one stopping them, eight days wide
    of the box's own ``max``.  A template restating a partition is a second
    place for it to be wrong, and adding a third arm for the matched-line bound
    would have made a two-way disagreement a three-way one.

    Args:
        earliest_movement: The account's earliest recorded movement day, or
            ``None`` where it records none.  **Taken rather than looked up**,
            so one render asks the question once and the ceiling and the
            sentence explaining it cannot come from two reads.
        earliest_assertion: The earliest day the owner has asserted a balance
            for, or ``None``.  The bound a code review added on 2026-08-31,
            after the ceiling offered TODAY on every account with no settled
            movement -- which is every investment, retirement and property
            account the developer holds.
        earliest_matched_line: The earliest day the account has matched a bank
            line on, or ``None``.  Plan step **balance:X-f3c-2b-2b**, and it is
            a SEPARATE term rather than folded into *earliest_movement*
            because it can be strictly EARLIER: a match settles every member on
            the LATEST of its bank days, so the group's first line predates the
            row that explains it.
        first_planned_row: The earliest still-projected recurring row books
            opening today would strand, or ``None``; see
            :func:`_opening_ceilings` for why today is the day asked.

    Returns:
        The binding :class:`OpeningCeiling` -- the EARLIEST of the owner's
        today, the day before the account's first recorded movement, the day
        before its first matched bank line, the day of its first asserted
        balance, and the day before the first planned row's compared day --
        carrying the sentence that names it.
    """
    return min(
        _opening_ceilings(
            earliest_movement, earliest_assertion, earliest_matched_line,
            first_planned_row,
        ),
        key=lambda ceiling: ceiling.day,
    )


def books_opening_context(account: Account) -> "dict | None":
    """Assemble the books-opening card's context, or ``None`` to render nothing.

    Called by :func:`app.routes.accounts.crud.edit_account` so the card is part
    of the page it lives on rather than a fragment the page fetches: nothing
    about an opening changes without a full round trip through the POST below,
    so there is no event for a fragment to refresh on.

    Args:
        account: The owned, attached :class:`~app.models.account.Account`.
            Caller owns the ownership check.

    Returns:
        The card's context -- ``opened_on``, ``equity`` (the amount OWED for a
        liability, whose card speaks owed: ``asks_owed``, plan step
        credit_card:CC-5-5b), ``declared``
        (whether a human stated the standing figure or the X-f3c-2a
        migration derived it) and ``ceiling``, the binding
        :class:`OpeningCeiling` carrying the date box's ``max`` and the
        sentence naming which of the FIVE bounds set it -- or ``None`` for
        an AMORTIZING account, whose opening is its loan's original principal,
        and where a broken invariant costs the card (no opening row, or a
        recurring rule that cannot be walked).
        ``None`` rather than a flag the template branches on: a card that must
        not be offered is absent, not disabled, which is the dead-end
        affordance rule ``anchor.anchor_form`` states.
    """
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return None
    # **The non-raising reader, so a broken invariant costs the card and not
    # the PAGE** (adversarial review, 2026-08-31).  ``account_opening_fact``
    # raises for an account carrying no opening row, and this builder is called
    # unconditionally by ``crud.edit_account`` -- which is the only surface
    # offering rename, archive and hard-delete.  Raising here would 500 that
    # page, so the one account that most needs repairing would be the one
    # account no door could reach.
    opening = cash_ledger.governing_account_opening(account.id)
    if opening is None:
        return None
    # ONE read of the account's earliest movement for this render, threaded to
    # both consumers: the date input's ceiling and the sentence that tells the
    # owner why it stops there.  Asking twice is this project's DRY violation
    # rather than a cost.
    earliest = cash_ledger.earliest_recorded_movement_day(account.id)
    first_assertion = cash_ledger.earliest_assertion_day(account.id)
    # The fourth bound (plan step **balance:X-f3c-2b-2b**), read here beside
    # the other two for the reason stated above them: one render asks each
    # question once, and the ceiling and the sentence explaining it come from
    # the same answer.
    first_matched = cash_ledger.earliest_matched_line_day(account.id)
    # The fifth (ruling R-PC88), from the one producer the door refuses by --
    # and, like the opening above, a broken invariant costs the card and not
    # the page: a stored rule the recurrence package cannot resolve or walk
    # (none a door writes) raises there, for any definition moving money in
    # the account, archived ones included.
    try:
        first_planned = first_row_an_opening_strands(
            account.id, display_today(), calendar_for(account.user_id),
        )
    except (RecurrenceResolutionError, RecurrenceGenerationError):
        logger.warning(
            "account %d's books-opening card is hidden: a recurring "
            "definition moving money in it has a rule that cannot be walked",
            account.id, exc_info=True,
        )
        return None
    return {
        "opened_on": opening.opened_on,
        # In the DOOR's language (plan step credit_card:CC-5-5b, ruling
        # R-CC52): a liability's card states and pre-fills what its books
        # opened OWING, so a card opened at -1,000.00 held reads 1,000.00 owed.
        # An asset's figure is its equity as stored.
        "equity": liability_sign.shown_figure(
            account.account_type, opening.opening_equity,
        ),
        "asks_owed": liability_sign.asks_owed(account.account_type),
        # Whether a HUMAN stated the standing figure.  The card says so,
        # because a ``migration_derived`` opening is the pre-X-f3c-2a inference
        # frozen and may be WRONG -- findings **N-275** and **N-379** measure
        # two of the seven production figures wrong against the owner's own
        # bank -- and this is the door for exactly that case.
        "declared": opening.source_id == ref_cache.account_opening_source_id(
            AccountOpeningSourceEnum.USER_DECLARED,
        ),
        # The ceiling and the sentence naming it, as ONE value: an owner
        # refused by a bound they cannot see reads it as the form being
        # broken, and a card that picked which bound to name would be picking
        # in a different order from the ``min`` that set the box's own ``max``.
        "ceiling": _latest_legal_opening_day(
            earliest, first_assertion, first_matched, first_planned,
        ),
    }


def _restatement_failure(account_id: int, message: str) -> ResponseReturnValue:
    """Flash *message* and return to the edit page carrying the card.

    The ONE rejection path this door has, and it is the flash-and-redirect
    shape every other write on that page uses (``crud.update_account``,
    ``crud.archive_account``, ``crud.hard_delete_account``) rather than a
    designed HTMX fragment: this is a full-page form POST, so the page it
    returns to re-renders the card from the database with the standing figures
    -- which is the correct thing for a refused submission to show, because
    nothing was written.

    Args:
        account_id: The account whose edit page to return to.
        message: The user-facing reason, already flattened to one sentence.

    Returns:
        The redirect response.
    """
    flash(message, "danger")
    return redirect(url_for("accounts.edit_account", account_id=account_id))


@accounts_bp.route("/accounts/<int:account_id>/opening", methods=["POST"])
@require_owner
def restate_opening(account_id):
    """Restate what an account's books opened with.

    Appends one ``budget.account_openings`` row through
    :func:`app.services.opening_service.apply_opening_restatement`, which --
    under the owner's write lock this POST's transaction took before the route
    ran (:mod:`app.db_transaction`, plan step ``balance:X-bn``) -- declines a
    submission that changes nothing (ruling **R-EQ**), re-bases the account's
    posted anchor corrections onto the new day and figure, and commits.

    **Every money and date refusal belongs to the service, and this route adds
    none of its own.**  The day is bounded there by all FIVE rules
    ``opening_service._reject_restatement_day`` applies -- not in the future,
    strictly before every movement the account records, strictly before every
    bank line it has matched (ruling **R-HG** for both), on or before the
    first balance the owner asserted, and stranding no still-projected
    recurring row (ruling **R-PC88**) -- so
    the card's date-input ``max`` is a convenience the browser applies first
    and never a second answer to the same question.  The kind gate is asked
    here as well, and that is not a duplicate for the reason
    ``anchor._true_up_request_gates`` gives: an account's KIND is EDITABLE, so
    a card rendered while the account was cash can be submitted after it became
    a loan, and the service's own refusal is a ``ValueError`` that would reach
    the owner as a 500.  The route's second gate is the card's MEANING
    (``_door_meaning.door_meaning_refusal``, ruling **R-CC61**): a card
    rendered under the other of asset and liability is refused before the
    figure is crossed into its held sign.

    **It tells the owner what a restatement does NOT do**, and that sentence is
    measured rather than cautious: the account's later assertions still say
    what they said, so the gap the restatement opens is booked as a correction
    against them.  On the developer's own archived Fidelity account, taking a
    ``$4,863.56`` opening to ``$0.00`` leaves the 2026-04-06 assertion booking a
    ``$5,363.56`` true-up and the asset returns in full.  An owner who is not
    told that reads the unchanged balance as the door having failed.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Account not found", 404

    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return _restatement_failure(account_id, LOAN_OPENING_REFUSAL)

    errors = _opening_schema.validate(request.form)
    if errors:
        return _restatement_failure(
            account_id, flatten_schema_errors(errors),
        )
    data = _opening_schema.load(request.form)
    # Ruling R-CC61: a card rendered under the other meaning (the account
    # crossed asset <-> liability in another tab) is refused before anything is
    # written, and the edit page it returns to re-renders the card under the
    # meaning the account has now.
    stale = door_meaning_refusal(account, data["asks_owed"])
    if stale is not None:
        return _restatement_failure(account_id, stale)
    asks_owed = data["asks_owed"]

    try:
        outcome = opening_service.apply_opening_restatement(
            account=account,
            opening=opening_service.BooksOpening(
                opened_on=data["opened_on"],
                # ``Decimal(str(...))`` rather than the schema's own object,
                # the construction ``anchor.true_up`` uses for the same reason:
                # the project builds money from a STRING so nothing can hand a
                # binary float into a monetary path.  Then the ONE crossing
                # this door makes (plan step credit_card:CC-5-5b, ruling
                # R-CC52): a liability's card asks what the books opened OWING,
                # and the service stores the held sign.
                equity=liability_sign.held_balance(
                    account.account_type,
                    Decimal(str(data["opening_equity"])),
                ),
            ),
        )
    except ValidationError as exc:
        return _restatement_failure(account_id, str(exc))

    # Both messages echo the figure as TYPED, which on a liability is the
    # amount owed -- so they say "owed" / "owing" rather than "holding".
    if outcome is OpeningRestatementOutcome.UNCHANGED:
        # Ruling R-EQ idempotent success: the submission states the opening
        # that already stands, so nothing was written and the service rolled
        # back.  Reported as success rather than as an error -- the state the
        # owner asked for is the state that stands -- but not as "recorded",
        # which would be false.
        flash(
            "These books already open on "
            f"{data['opened_on'].isoformat()} at ${data['opening_equity']}"
            f"{' owed' if asks_owed else ''}. Nothing was changed.",
            "info",
        )
    else:
        flash(
            "Books restated: this account now opens on "
            f"{data['opened_on'].isoformat()} "
            f"{'owing' if asks_owed else 'holding'} "
            f"${data['opening_equity']}. Balances you recorded afterwards are "
            "unchanged, so the difference shows up as a correction against "
            "them -- reported as a gain or as interest on a savings, "
            "investment or property account. Your balance stays right; "
            "restating those later balances is what clears it.",
            "success",
        )
    # No log line here.  ``opening_service.stage_account_opening`` logs the
    # account, the day, the figure AND the provenance, and a route line naming
    # a strict subset of that is noise that reads as corroboration.
    return redirect(url_for("accounts.edit_account", account_id=account_id))
