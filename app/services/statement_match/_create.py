"""The door that turns a bank line the app has no row for INTO a purchase.

Ruling **R-FS**'s third shape, plan step ``bank_import:X-f6a-3b``.  **It MOVES
MONEY**: a line recorded here becomes a cash movement the app did not have,
dated the day the bank posted it.

**The card-swipe lines an envelope aggregates have no counterpart to match**,
because the app records a period's groceries as one envelope and the bank
records every swipe.  Measured on the developer's own 2026-08-16 statement
against a 2026-08-18 production clone: after every proposal the matcher offers,
**91 unmatched outflows** remain -- 74 of them card swipes worth `$3,383.49`,
the case R-FS names -- and they are not the same money at a different grain: in
period 9 the app holds 8 debit purchases, the matcher pairs ALL 8, and the bank
then shows 12 more the app has nothing for.  Over the assertion span the app's
own records state `+$3,000.46` of movement where the bank states `+$558.28`.

**Every bank line becomes a PURCHASE, and this door never creates a bare
transaction.**  A purchase is the app's record of one payment; a transaction is
a budget line that reserves.  Recording a payment as a budget line would
collapse the two facts a purchase keeps apart, and it would give a later
statement line nowhere to go.  So the only question the owner answers is which
budget line CONTAINS it, and there are exactly two answers:

* an existing envelope, from the set :func:`~._destinations.destinations_for` offers;
* a NEW envelope this door creates in the line's own pay period, named from
  what the bank called it.

**The second arm always creates an ENVELOPE rather than a plain row**, so an
unbudgeted expense gets a container that can take later purchases too, and so
the app's shape does not depend on whether a category happened to be budgeted.

**Nothing here decides which** (ruling **R-FP**): the owner picks, and the
picking is what makes this a review rather than an import that rewrites a
budget.

**...and since plan step ``bank_import:X-ge`` the owner may have picked
EARLIER** (ruling **R-GH**).  Consent splits by ACT CLASS: a standing merchant
rule is the owner saying where that merchant's money goes, so an import may
open this door for a NEW swipe line without a second act -- and every act that
would MODIFY a row they made by hand still needs its tick, which is
:func:`~._accept.accept_match`'s door and not this one.  Nothing about the
purchase changes: the destination is resolved from the same offer set, the
figure and both days come from the same recorded line, and the act records
WHICH consent it had (``applied_by_rule``) so an owner reading the receipt can
tell the two apart and undo either.

**...but some lines have no answer at all, and this door refuses them** (ruling
**R-GJ**, plan step ``bank_import:X-ga``).  A merchant the owner has answered
*never a purchase* for, and one a SOURCE files as a payment to a credit card
that they have not answered for, are BARRED: no destination makes such a line
legal, because the money it moved is already in the budget in another shape.
The screen renders no create control for one, on EITHER of the two lists the
pass files a barred line in (:attr:`~._reads.ReviewSet.parked` and
``answered_never``, split at plan step ``bank_import:X-gj-4c``), and
:func:`~._bars.reject_barred_line` refuses it at this door, which is the half a
crafted body or a stale page reaches.  **This door is blind to which list**,
because it reads :meth:`~._bars.CreationBars.bar_for` rather than the screen's
partition -- so the split moved no refusal.  Measured on the developer's own
dev database: nine Capital One ACH payments became `$7,412.94` of purchases in
eight new envelopes, beside 22 ``CC Payback`` rows RECORDING `$6,286.46` of the
same card's spending, in one pass, past a warning paragraph.  **Those rows are
still standing**; closing this door repairs nothing behind it, which is
``bank_import:X-gb``'s.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from app.services.cash_ledger import movement_figure_for
from app.services.settle_day import SettleDay
from app.services.stated_figure import StatedFigure
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_LINE_RECORDED,
    log_event,
)

from ._accept import MatchContent, record_match
from ._bars import MerchantAnswers, reject_barred_line
from ._container import (
    MintedEnvelopes,
    close_container,
    reject_ambiguous_destination,
    reject_incomplete_new_envelope,
    resolve_destination,
    ActReads,
)
from ._resolve import load_lines
from ._candidates import MatchedSubjects, matched_subjects
from ._creations import (
    CreatedPurchase,
    CreatedSubject,
    PurchaseCreation,
)
from ._offers import day_made, merchant_label
from ._subjects import CandidateRow, RowKind
from ._outcome import FiledMerchant
from ._reads import as_bank_line
from ._naming import name_the_filed_definition
from ._rules import LinePipeline, is_inflow, pipeline_for
from ._scope import ReviewScope, reject_impossible_days
from ._valuation import purchase_candidate

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.pay_calendar import PayCalendar

_logger = logging.getLogger(__name__)


def _load_line(
    creation: PurchaseCreation,
    matched: MatchedSubjects,
    scope: ReviewScope,
    answers: MerchantAnswers,
) -> BankStatementLine:
    """Return the submitted line, refusing one this door may not record.

    **The account scope and the already-matched refusal are
    :func:`~._resolve.load_lines`', not restated here** (plan step
    ``bank_import:X-f6a-3c-2``).  This door records a match like every other,
    so *is this line on this account, and has something already claimed it* has
    to be ONE answer; it carried a second copy of both while that function was
    private, which is two places for a refusal to stop firing.  The
    already-matched half matters here for the same reason it does there:
    ``uq_statement_match_members_line`` refuses the second act anyway, but it
    arrives as an ``IntegrityError`` after a purchase has been created, which
    reaches the user as "Something went wrong" and logs a traceback for an
    ordinary stale page.

    The third refusal IS this door's own, because it is about what THIS DOOR
    builds rather than about matching: the line must be one
    :func:`~._rules.pipeline_for` routes to
    :attr:`~._rules.LinePipeline.PURCHASE`.

    **It was *the line must be money LEAVING*, and neither half of that is the
    rule any more** (ruling **bank_import:R-II**, plan step
    ``bank_import:X-gj-2b``).  The REASON was the schema -- *a purchase is an
    expense (``ck_transaction_entries_positive_amount``), so a deposit or a
    refund cannot become one* -- and that CHECK is ``amount <> 0`` now, so a
    refund IS a purchase the table can hold: a negative one, against the
    envelope its merchant rule names.  The TEST was the sign, and plan step
    ``bank_import:X-gj-2b-2`` built the act that files such a line, so the sign
    no longer decides: an inflow whose merchant carries a container answer is a
    refund this door owns, and every other inflow is
    :func:`~._income.record_income_from_line`'s.  Asking the dispatcher is what
    keeps this refusal and the screen's own partition one answer.

    **What is still refused, and why it is not a guess** -- an inflow the
    owner has said NOTHING about.  Filing one against a budget line would name
    a container they never gave, which is what ruling **R-HX** refused.  16 of
    the developer's own unexplained lines are inflows, so this is the ordinary
    shape rather than a crafted request.

    **What it SENDS the owner to changed at ruling bank_import:R-GW**, and the sentence
    with it.  It used to say *match it to the row it belongs to instead*,
    which was the only other act there was -- and for an inflow no row
    explains, it was advice to do something impossible: eight of the
    developer's own deposits are smaller than the smallest row the match form
    offers.  :func:`~._income.record_income_from_line` is the other half now,
    so the refusal names both and the two doors are total over the lines the
    schema allows (``ck_bank_statement_lines_amount_real_nonzero``).

    Args:
        creation: What the owner submitted.
        matched: What this account's matches have already claimed, as of this
            act.
        scope: The pass, which is the ONE statement of which account's lines
            may be reached.
        answers: What the owner has said about this account's merchants
            (:class:`~._bars.MerchantAnswers`), which is what decides whether
            an INFLOW is a refund this door may file -- see the refusal below.

    Returns:
        The line.

    Raises:
        ValidationError: On any of the three.
    """
    line = load_lines(
        scope.account_id, frozenset({creation.line_id}), matched,
        for_write=True,
    )[0]
    # **An inflow is a purchase only where the owner's own rule claims it**
    # (plan step ``bank_import:X-gj-2b-2``, ruling **R-HT(a)**).  A merchant
    # credit from a merchant whose SPENDING the owner has placed is a refund
    # back into that same container; a deposit nobody has claimed is not, and
    # filing one against a budget line would be the guess ruling **R-HX**
    # refused.  Asked through :func:`~._rules.pipeline_for`, which is the SAME
    # function :func:`~._leftovers.leftovers` routes by -- so this door refuses
    # exactly the lines the screen renders no create control for, and the two
    # cannot come to disagree.
    rule = answers.view.rules.get(line.merchant_id)
    pipeline = pipeline_for(
        amount=line.amount,
        answer=rule.answer if rule is not None else None,
    )
    if pipeline is not LinePipeline.PURCHASE:
        raise ValidationError(
            "That line is money coming IN, and you have not said where this "
            "merchant's spending goes -- so it is not a refund this app can "
            "file. Record it as income, or match it to the row it belongs to, "
            "instead. Nothing was changed."
        )
    return line


def _observed(line: BankStatementLine) -> SettleDay:
    """Return *line*'s posting day as a day a bank statement SHOWED.

    One statement of the basis this module writes, for the three places that
    write it -- the purchase born from the line, and :func:`_close_day`'s two
    arms -- because a second spelling of "what kind of day is a bank line's
    posting day" is the shape finding **N-332** is about one tier down.

    Args:
        line: The bank line being recorded.

    Returns:
        A :class:`~app.services.settle_day.SettleDay` over ``line.posted_on`` on
        the ``observed`` basis.
    """
    return SettleDay(
        day=line.posted_on, basis=SettledDayBasisEnum.OBSERVED,
    )


def _made_by_this_act(
    candidate: CandidateRow, envelope: Transaction, container_created: bool,
) -> "tuple[CreatedSubject, ...]":
    """Return what this act BROUGHT INTO EXISTENCE, which is not what it names.

    Ruling **R-GG**, plan step ``bank_import:X-f6f``.  The purchase is both --
    created, and named, because it is what the bank line IS.  The envelope is
    created and never named: naming a container beside its own purchase would
    count the same money twice, so the act names the purchase alone.
    That asymmetry is exactly why the record is its own relation rather than a
    column on the membership, and it is why this function exists rather than
    the door passing its own rows through.

    **The envelope's revision is read AFTER the act's writes are flushed**, by
    the caller: a container is written twice here -- born Projected, then
    closed on the bank's day -- so the revision this act LEFT is the only one
    an undo can compare against without reporting the door's own second write
    as somebody else's edit.

    Args:
        candidate: The purchase this act created, already priced.
        envelope: The budget line it went into.
        container_created: Whether THIS act made that budget line.

    Returns:
        One :class:`~._creations.CreatedSubject` per row, purchase first.
    """
    purchase = CreatedSubject.of(candidate)
    if not container_created:
        return (purchase,)
    return (
        purchase,
        CreatedSubject(
            kind=RowKind.TRANSACTION,
            row_id=envelope.id,
            version_id=envelope.version_id,
        ),
    )


def _born_purchase(
    line: BankStatementLine,
    envelope: Transaction,
    made_on: date,
    observed: SettleDay,
    scope: ReviewScope,
) -> "TransactionEntry":
    """Create the purchase this bank line IS, carrying BOTH of its days.

    Ruling **R-FW**.  Recording them in one ``create_entry`` call rather than
    creating and then updating is what keeps a purchase the bank has already
    taken from existing, even briefly, as an outstanding one -- and it is why
    the match this door records afterwards moves no day: the row already
    carries the ones the bank stated.

    Args:
        line: The recorded line, already proved recordable.
        envelope: The container it goes in.
        made_on: The day the bank says it was MADE, which is the purchase's own
            budget clock (:func:`_made_on`).
        observed: The day the bank TOOK it, on the ``observed`` basis (plan step
            **X-az**) -- the bank line IS why this purchase exists, so its
            posting day is a day a statement showed rather than a bound or a day
            the owner typed.  **This is the only door that BORNS a purchase
            carrying a posting day, and the only one whose basis could never be
            anything else.**  Taken rather than built, because the container's
            own close needs the identical value and a second construction is the
            duplication :func:`_observed` exists to remove.
        scope: The pass, which is the ONE statement of whose account this is.

    Returns:
        The staged :class:`~app.models.transaction_entry.TransactionEntry`.
    """
    return entry_service.create_entry(
        transaction_id=envelope.id,
        user_id=scope.owner_id,
        details=entry_service.EntryDetails(
            # **The line's own figure, in the ENVELOPE's direction**
            # (``cash_ledger.movement_figure_for``, plan step X-bi-3b): the
            # bank states an outflow as negative and a purchase under an
            # expense records what it cost, so the conversion is the sign
            # rule read the other way -- TOTAL over both directions without
            # a branch, which is why ruling **bank_import:R-II** needed no
            # new arithmetic here: an inflow of ``+28.29`` becomes a refund
            # of ``-28.29`` by the same expression.  Plan step
            # ``bank_import:X-gj-2b-2`` is what lets a refund reach this line,
            # and it needed nothing added here to file one.  It was spelled
            # ``-line.amount`` here, the expense arm alone, until X-bi-3b.
            # The BANK wrote this figure -- the line IS why the purchase
            # exists -- and the builder says so with the figure itself (plan
            # step X-bi-3e-1, ruling R-BAL61) rather than leaving the door to
            # read it off the ``observed`` day beside it.
            figure=StatedFigure(
                amount=movement_figure_for(
                    envelope, Decimal(str(line.amount)),
                ),
                source=MovementFigureSourceEnum.OBSERVED,
            ),
            # What the BANK NAMES the merchant, not the whole line
            # (:func:`~._offers.merchant_label`).  The app's own purchases are
            # named "Walmart" and "Food Lion", and a purchase called
            # ``POINT OF SALE DEBIT L340 DATE 08-13 Amazon.com*5H2RA5V...``
            # would be the only row in the entries list nobody can read.  The
            # bank's full wording is not lost: it stays on the statement line,
            # which the match this door records ties to this purchase.
            # **The LABEL, not the key** (plan step X-f6a-3d): it falls back to
            # the description for a source that names no merchant, because
            # ``transaction_entries.description`` is NOT NULL and this door
            # calls ``create_entry`` directly.
            description=merchant_label(
                line.merchant_name, line.description,
            )[:200],
            purchased_on=made_on,
            settle_day=observed,
        ),
    )


def _match_content(
    entry: "TransactionEntry",
    line: BankStatementLine,
    envelope: Transaction,
    created: bool,
    calendar: "PayCalendar",
) -> MatchContent:
    """Return what this act ASSERTS and what it BROUGHT INTO EXISTENCE.

    **Two relations, built together because they are decided together** (plan
    step ``bank_import:X-f6f``, ruling **R-GG**), and read from ONE candidate:
    :func:`~._valuation.purchase_candidate` prices the row this act just made,
    and both the membership and the creation record are that same value rather
    than two derivations of it.

    **No residual, and it can never have one**: this door built the purchase at
    the line's own figure, so the two sides agree to the cent by construction
    and there is no difference for ruling **R-FN**'s row to record (plan step
    ``bank_import:X-f6d-4``).

    **It reads the candidate AFTER the caller has flushed**, which is that
    step's own rule: a container this door creates is written twice, and the
    revision an undo compares against has to be the one this act LEFT.

    Args:
        entry: The purchase this act created, already flushed.
        line: The bank line it explains.
        envelope: The budget line it went into.
        created: Whether THIS act made that budget line.
        calendar: The pass's calendar, which the candidate's placement is
            read from -- the constructor takes it since plan step
            ``bank_import:X-gz``, and this door hands it the one the pass
            holds rather than loading a second.

    Returns:
        The :class:`~._accept.MatchContent`.
    """
    candidate = purchase_candidate(entry, calendar)
    return MatchContent(
        lines=[line], rows=[candidate],
        created=_made_by_this_act(candidate, envelope, created),
    )


def create_purchase_from_line(
    creation: PurchaseCreation,
    scope: ReviewScope,
    minted: MintedEnvelopes,
    answers: MerchantAnswers,
    *,
    applied_by_rule: bool,
) -> CreatedPurchase:
    """Record one bank line as a purchase, and match the line to it.

    The whole act, in the order its refusals have to happen: the line is
    checked, the merchant is checked against ruling **R-GJ**'s bars, the
    destination is resolved against the set the screen may offer, and only then
    is anything staged.  The match itself goes through
    :func:`~._accept.record_match`, so the correspondence is written by the same
    function that writes every other one -- ruling **R-FT**'s table, ruling
    **R-FV**'s identity-only rule, and every guard those already carry.

    **It records the row it just built rather than asking a door to find it**
    (plan step ``bank_import:X-f6a-3c-2``).  It called ``accept_match`` with the
    new purchase's id until this step, which re-derived all 827 of the account's
    candidate rows to prove that id was in scope -- 3.593 s per line on the
    developer's own clone, over 91 of them, and finding **N-309**'s third payer.
    Worse, it made the act unshareable: a purchase created inside a pass cannot
    be in an offer set derived before the pass, so running a whole statement
    against one derivation refused **all 91** of these lines as "no longer
    available to match".  A door that created a row does not need to prove that
    row is offerable, so it states the candidate itself, through the same
    :func:`~._valuation.purchase_candidate` the offer set is built from.

    **The purchase is BORN carrying both of its days** (ruling **R-FW**): the
    day the bank says it was made, and the day the bank took the money.
    Recording them in one ``create_entry`` call rather than creating and then
    updating is what keeps a purchase the bank has already taken from existing,
    even briefly, as an outstanding one.  It is why the match this door records
    moves no day: the row already carries the ones the bank stated.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        creation: What the owner submitted: one line, and one destination.
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).
            **Required rather than defaulted**, for the reason
            :func:`~._accept.accept_match`'s is.
        answers: What the owner has said about this account's merchants
            (:class:`~._bars.MerchantAnswers`) -- the stated rules and ruling
            **R-GJ**'s bars, derived ONCE by the pass and read here rather than
            re-derived, which is the rule :class:`MintedEnvelopes` states
            beside it.  It replaced a bare ``bars`` argument at plan step
            ``bank_import:X-gj-2b-2``, when this door also needed the ANSWERS
            to tell a refund from a deposit: the two come from one read of
            ``merchant_rules`` and passing them separately let a caller supply
            them from two instants.
        minted: What this REQUEST has already created
            (:class:`MintedEnvelopes`), so one press mints one definition
            per answer and places one row of it per pay period rather than
            one per line (finding **N-327**).  **Required rather than
            defaulted for the same reason
            *scope* is**: a default would silently mean *converge with
            nothing*, and the caller that forgot it would mint the fragments
            this parameter exists to stop.
        applied_by_rule: Whether a STANDING RULE performed this act rather than
            a person ticking it (ruling **R-GT**, plan step
            ``bank_import:X-ge``).  **Keyword-only and with no default**, for
            the reason :func:`~._accept.record_match`'s own flag has neither:
            its two values are *the owner agreed to this* and *the app did it
            on their behalf*, so a positional ``False`` says nothing at a call
            site and a default would let a future door claim consent by
            omission.  It is the PASS's answer
            (:attr:`~._batch.Consent.applied_by_rule`) rather than one this
            door derives -- both of its entrances create the same purchase in
            the same place, and the only thing that differs is who asked.
            **This door has TWO entrances since X-ge**: the review screen's own
            destination select, one line at a time, and an import filing a new
            swipe under a rule the owner stated.

    Returns:
        The :class:`CreatedPurchase`.

    Raises:
        ValidationError: On any of this door's refusals or a settle door's.
            A 400: every one is reachable by an ordinary owner working from a
            stale page.
    """
    reject_ambiguous_destination(creation)
    reject_incomplete_new_envelope(creation)
    # ONE read of what this account's matches have claimed, for this act:
    # the line refusal narrows with it.  The destination refusal re-asks the
    # row itself (``_destinations.current_destinations``).
    matched = matched_subjects(scope.account_id)
    line = _load_line(creation, matched, scope, answers)
    # **Before anything is staged**, and before the destination is even looked
    # at: for a barred line there is no destination that would make it legal,
    # so resolving one first would answer a request that may not be made with a
    # sentence about the answer it gave (ruling **R-GJ**).
    reject_barred_line(line, answers.bars)
    # **Before the destination is resolved and before anything is staged**
    # (plan step **balance:X-f3c-2b-2b**, finding **N-383**).  A line the
    # account's books cannot hold is money already inside its opening equity,
    # so there is no destination that would make recording it legal -- the
    # same reason the bar above runs where it does.  ``create_entry``'s settle
    # verb refuses the day too, and refuses it AFTER ``resolve_destination``
    # may have minted an envelope for a purchase that will never exist; this
    # module's own promise is that every refusal fires before anything is
    # written (:func:`~._income.record_income_from_line`), and the savepoint
    # that would have covered it is the batch's rather than this door's.
    scope.reject_line_before_books_open(line.posted_on, "this purchase")
    # **The third refusal that has to fire here for the same reason** (plan
    # step ``bank_import:X-gm``, finding **N-325**).  A line the bank dates
    # MADE after it POSTED can become no purchase at all: ``create_entry``
    # refuses the resulting pair through
    # ``entry_service._reject_settled_before_purchase``, backed by
    # ``ck_transaction_entries_settled_not_before_purchase`` -- and refuses it
    # AFTER ``resolve_destination`` may have minted an envelope, which is
    # exactly what the comment above says this module does not do.  The screen
    # withholds the ADD control on the same sentence
    # (:func:`~._leftovers._add_refusal`), so a stale page and a crafted body
    # are answered in the same words.  **The BankLine is built rather than the
    # comparison re-spelled**: the predicate is
    # :attr:`~._offers.BankLine.states_impossible_days` and this door asks it
    # through the same view model :func:`~._skipping.skipped_acts` builds.
    reject_impossible_days(as_bank_line(line))
    # The day the bank says it was MADE, by the package's one spelling
    # (:func:`~._offers.day_made`; this module spelled it a third time until
    # plan step ``bank_import:X-f6b-2``).  SECU states one on 182 of 361
    # lines.
    made_on = day_made(line)
    # ONE construction of the bank's own day for this act, for the two
    # writers that need it: the purchase is born carrying it, and the
    # container may close on it.  A second construction is the
    # duplication :func:`_observed` exists to remove.
    observed = _observed(line)

    # ONE period for both arms, resolved once: it is where the purchase is
    # BUDGETED, so it decides which envelopes may hold it and which period a
    # new one is created in.  Resolving it per arm is how the two came to
    # disagree.
    # **The day it was MADE decides, not the day it posted**, and the
    # difference is real at a period boundary: a swipe made on the last
    # day of one pay period and posted on the first day of the next
    # belongs to the budget of the period it was made in.
    # ``purchased_on`` is the budget clock and its container's period is
    # what the app budgets against, so placing the row by the posting day
    # would file already-spent money against the wrong paycheck -- and
    # would raise the entry list's out-of-period warning
    # (``entry_service.entry_list_view``, which asks
    # ``DerivedPeriod.covers``) on a row this door had just built.
    period = scope.period_holding(made_on, "this purchase")
    envelope, created = resolve_destination(
        creation, period,
        ActReads(
            scope=scope, minted=minted,
            placeable=answers.view.placeable_templates,
        ),
    )

    entry = _born_purchase(line, envelope, made_on, observed, scope)
    close_container(creation, observed, envelope, created)

    # **The act's own writes are FLUSHED before its creation records are
    # read** (plan step ``bank_import:X-f6f``).  A container this door creates
    # is written TWICE -- born Projected, then closed on the bank's day -- and
    # ``version_id`` only reaches the instance when the UPDATE is emitted, so
    # reading it before the flush would record the revision this act found
    # rather than the one it left, and the undo would then report its own
    # second write as somebody else's edit.
    db.session.flush()
    accepted = record_match(
        scope,
        _match_content(entry, line, envelope, created, scope.calendar),
        # **The PASS's own answer, threaded rather than decided here** (ruling
        # **R-GT**).  This door has two entrances since plan step
        # ``bank_import:X-ge`` -- the review screen's destination select and an
        # import filing a new swipe under a stated rule -- and they build the
        # identical purchase in the identical place.  The only fact that
        # differs is who asked, which is why it arrives as an argument instead
        # of being read off anything here.
        applied_by_rule=applied_by_rule,
    )

    recorded = CreatedPurchase(
        entry_id=entry.id,
        transaction_id=envelope.id,
        match_id=accepted.match_id,
        envelope_label=envelope.name,
        envelope_created=created,
        template_id=envelope.template_id,
        # The merchant's NEW-ENVELOPE answer coming to NAME the definition
        # filed into (leaf 7b-3 of balance:X-bi-7b): a rule write in this
        # act's own transaction, ordered after the match only because the
        # purchase and the match are what the rest of this constructor
        # reads; nothing above reads the rule.
        answer_named=name_the_filed_definition(line, envelope, answers),
        # **From the ROW, not from a second copy of the arithmetic.**
        # :func:`_born_purchase` computed the figure from the line and
        # ``create_entry`` stored it, so reading it back is the one place it is
        # stated -- and a receipt that recomputed it could report a figure the
        # database does not hold.
        amount=entry.amount,
        posts_on=line.posted_on,
        made_on=made_on,
        pay_period_id=period.period_id,
        # **The DIRECTION, stated where the line is in hand** (ruling
        # **bank_import:R-II**, plan step ``bank_import:X-gj-2b-3``).  Asked
        # through :func:`~._rules.is_inflow`, this package's one statement of
        # the bank's sign convention, so the receipt reads a fact this door
        # established rather than re-deriving a direction from the purchase's
        # own sign -- the rule ``_panel.AddAct`` states for the card and
        # ``_cards.creatable_card`` already follows.
        records_a_refund=is_inflow(line.amount),
        # **The MERCHANT, off the row this door holds locked** (plan step
        # ``bank_import:X-gx``, finding **BI-495**).  ``merchant_name`` is
        # ``None`` exactly when ``merchant_id`` is, so the pair is one fact;
        # the receipt's standing-rule offer reads it from here rather than
        # from the page's derivation, which hydrated the line before the lock.
        merchant=(
            None if line.merchant_id is None
            else FiledMerchant(
                merchant_id=line.merchant_id, name=line.merchant_name,
            )
        ),
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_LINE_RECORDED, BUSINESS,
        "A bank statement line was recorded as a purchase.",
        user_id=scope.owner_id,
        account_id=scope.account_id,
        line_id=line.id,
        entry_id=recorded.entry_id,
        transaction_id=recorded.transaction_id,
        match_id=recorded.match_id,
        envelope_created=created,
        amount=str(recorded.amount),
        posts_on=recorded.posts_on.isoformat(),
        made_on=recorded.made_on.isoformat(),
    )
    return recorded
