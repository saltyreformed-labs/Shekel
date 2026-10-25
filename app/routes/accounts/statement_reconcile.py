"""
Shekel Budget App -- The RECONCILE page, and the three doors it posts to

"What is each of these bank lines?" -- one page on four verbs, and since plan
step ``bank_import:X-gi-2`` the ONLY one: it replaced the review queue, the
register and the hand-build workbench, and that step deleted all three.  Plan
step ``bank_import:X-gj-1b``; the direction was locked at Loop A round 4 on
2026-08-29 and is ``docs/design/bank_import_audit.md``.  Its rulings are
**bank_import:R-HP** through **R-HX** (ruling cards in the tracker), plus
**bank_import:R-IA** (the accept door exempts no shape) and
**bank_import:R-IB** (a standing rule is offered once per merchant, on the
receipt).  **Both ids were minted in the ``balance`` arc the same day**, so
every citation of them here names its arc.

**IT MOVES MONEY, through doors that already exist.**  Apply posts the OK'd
cards through :func:`~app.services.statement_match.apply_reviewed` -- the door
the review queue and the workbench posted through until plan step
``bank_import:X-gi-2`` deleted them, with the same savepoint-per-item
policy (**R-FZ(a)**) and the same receipt; UNDO posts through
:func:`~._statement_release.release_and_return`, the same door the import
receipt uses (plan step ``bank_import:X-gj-1c``); and the
RECEIPT's per-merchant standing-rule offer posts through
:func:`~._statement_rules.record_submitted_rules`, which moves none.  **This
module opens no door of its own**, which is what lets a whole screen ship
without a migration and without a new money path.

**Six routes, and only ONE of them re-renders without writing.**  The page and
Apply are the pair; the two UNDOs are plain POST-redirect-GETs back to the tab
they were pressed on -- a match's, which destroys rows the act created, and a
skip's, which destroys only the decision (plan step ``bank_import:X-gj-4c-2``);
the standing-rule offer's own door writes ``budget.merchant_rules`` and commits
(ruling **bank_import:R-IB**); and the MATCH pane re-renders one card's
candidate rows and what the ticked ones come to, writing nothing.  *It said
FIVE until the skip's undo landed*, and the count is corrected here rather than
left to a reader because the sentence goes on to enumerate them BY POSITION --
the same trap the parenthetical below already records this paragraph falling
into once.
*(This paragraph said "three routes, and the third is a READ" until
**bank_import:R-IB** added the rule door, at which point the third route in
file order was the one that WRITES -- a reader counting routes would have
mapped the sentence onto the wrong one.)*  It is a POST because it carries a
list of ids and a CSRF token, not because it changes anything -- the reason
the retired workbench's own live-totals endpoint was one.  **The alternative
was measured and refused**: rendering every card's candidate rows with the
page is 67 rows in 18 cards at that surface's measured 991 bytes a row,
~1.2 MB, which is finding **N-374** rebuilt one surface later.

**ONE card's rows DO render with the page, behind ``?open=<line_id>``** (plan
step ``bank_import:X-gi-1``, ruling **bank_import:R-KA**), and that is not the
refused alternative: it is one card rather than eighteen, on a request that
exists only because the owner asked for it.  Why that render reaches EVERY
unexplained row where the fragment reaches one pay period is argued once, in
:mod:`app.services.statement_match._opened`.

**It serves every tab the service builds** (plan step ``bank_import:X-gj-1c``).
The two whose cards are ACTS already applied -- Explained and Filed by rules --
arrived with that step, and with them the ``_TABS_SERVED`` tuple that had 404'd
them: a subset constant equal to the whole enum guards nothing.  The THREE
kinds of card are three values in the service and three partials here -- plan
step ``bank_import:X-gj-4c-2`` added the recorded SKIP, which is neither a bank
line nor a match -- and both an act's Undo and a skip's are a `form`, so those
two tabs render OUTSIDE the Apply form rather than inside it.

**The old routes stayed alive beside this page** until plan step
``bank_import:X-gi-2`` deleted them, which is ruling **R-HU**'s own
sequencing: every door this screen posts to was one already tested, nothing
was removed on the way in, and the deletion came only once ``X-gi-1`` had
repointed the last inbound link.  What ``X-gj-1c`` had already removed is the
register's REASON to exist -- the acts it listed are these two tabs, with the
same bound, the same *show the other N* link and the same Undo.

Services boundary: this module owns the HTTP-shaped concerns -- ownership,
form parsing, fragment rendering, URLs -- and delegates every read and write
to :mod:`app.services.statement_match`.
"""

import logging
from dataclasses import dataclass

from flask import abort, render_template, request, url_for
from flask_login import current_user
from app.exceptions import ValidationError
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts._reconcile_query import (
    asked_for_everything,
    asked_to_open,
    opened_ask,
    read_match,
    requested_tab,
)
from app.routes.accounts._statement_doors import (
    StatementDoorContext,
    fragment_door,
    log_pass_applied,
    refusal_sentence,
    run_one_id_door,
    run_statement_fragment_door,
    submitted_batch,
    submitted_item_count,
)
from app.routes.accounts._statement_release import release_and_return
from app.routes.accounts._statement_rules import record_submitted_rules
from app.schemas.validation.statement_reconcile import reconcile_payload
from app.schemas.validation.statements import (
    StatementBatchSchema,
    StatementSkipReleaseSchema,
)
from app.services import balance_at, bank_agreement
from app.services.category_service import list_active_categories
from app.services.statement_match import (
    REGISTER_LIMIT,
    MatchAsk,
    MatchReach,
    ReviewScope,
    RuleDoorAccepts,
    RuleView,
    apply_reviewed,
    opened_match,
    reconcile_page,
    refused_match,
    review_set,
    rules_worth_offering,
    unskip_line,
)
from app.utils.auth_helpers import require_owner
from app.utils.error_fragments import designed_error

_logger = logging.getLogger(__name__)

#: One schema instance each, constructed at import like every sibling's.  The
#: batch schema grades a whole pass.  **The match schema is
#: :mod:`._reconcile_query`'s** since plan step ``bank_import:X-gi-2a``: it
#: grades the ONE card a body is read for -- the card the live-difference
#: fragment re-prices, and the card ``?open=`` names on Apply -- and it is
#: the same schema the batch nests, so a card priced by either render and
#: the same card applied by the pass are graded by one set of rules.
_batch_schema = StatementBatchSchema()

#: The Skipped tab's Undo names ONE act, exactly as the settled tabs' does, and
#: it is a different schema over a different table (plan step
#: ``bank_import:X-gj-4c-2``).
_unskip_schema = StatementSkipReleaseSchema()

#: The partial the page, the Apply door and every refusal arm render.  ONE
#: template, so what htmx swaps in after a POST cannot drift from what a
#: reload shows -- the discipline all three sibling statement surfaces keep.
_BODY = "accounts/_statement_reconcile_body.html"

#: The MATCH tab's own body: the candidate rows, and what the ticked ones come
#: to.
_MATCH_PANE = "accounts/_statement_reconcile_match.html"

#: What a database failure tells the owner.  It names no table -- the
#: traceback goes to the log -- and it ends the way every refusal in this
#: package does, which is true here because the route owns the unit of work: a
#: pass that could not commit has written nothing.
_DB_ERROR_MESSAGE = (
    "Something went wrong applying that, and nothing was changed.  Here is "
    "where you were."
)

#: What the screen says about a card the owner OK'd that named no act.  It is
#: reachable from a browser -- pressing the panel's ADD button with nothing
#: chosen -- so it may not be a pass-level refusal, which would cost every
#: other OK on the page (**R-FZ(a)**), and it may not be a silent drop, which
#: would leave a press unanswered.
_OK_WITH_NO_ACT = (
    "You pressed OK on {count} card(s) without choosing what to do with "
    "them, so nothing was recorded for those: bank line(s) {lines}. Open the "
    "card and pick a destination, or a row to match it against."
)





def _chip_href(account_id: int, chip) -> "str | None":
    """Return where a holding chip's count leads, or ``None``.

    **The route owns this and the service may not**: a chip states WHICH tab
    owns its lines (:attr:`~app.services.statement_match.Tab`), and turning
    that into a URL is the one fact a service is not allowed to build.

    **Every chip that names a tab now leads to it**, which is what plan step
    ``bank_import:X-gj-1c`` left when it deleted the *already explained* chip
    and served the last two tabs.  This function carried a third arm sending
    that chip to the REGISTER, because the tab it named was not built; the
    chip itself is gone (:func:`~app.services.statement_match._reconcile
    ._chips` says why -- its count was the union of two tabs), and with every
    tab served there is no chip left that could name an unrenderable one.

    Args:
        account_id: The account this page is about.
        chip: The :class:`~app.services.statement_match._reconcile.HoldingChip`.

    Returns:
        The URL, or ``None`` for a chip that states a fact with no way in --
        the lines older than the pay calendar, which no surface lists and
        whose remedy is a pay schedule rather than an account.
    """
    if chip.tab is None:
        return None
    return url_for(
        "accounts.statement_reconcile",
        account_id=account_id, tab=chip.tab.value,
    )


@dataclass(frozen=True)
class ReconcilePass:
    """What one press of Apply did, and what it earns the right to ask.

    **ONE act now, and that is ruling bank_import:R-IB.**  This carried two --
    the money pass and the standing rules the card's *always, for this
    merchant* box asked for -- until the developer's ruling of 2026-08-30
    moved the rule offer onto the RECEIPT.  The two were run in one
    transaction so a rule could not survive a rolled-back pass; what the
    ruling saw is that the rule was derived from what was OK'd rather than
    from what LANDED, so a per-item refusal rolling back in its own savepoint
    left the rule standing anyway.  There is no second write here to sequence.

    Attributes:
        batch: The :class:`~app.services.statement_match.BatchOutcome` the
            money door applied.
        offers: One :class:`~app.services.statement_match.RuleOffer` per
            merchant whose purchases this pass RECORDED, in the order it
            applied them -- what the receipt may ask the owner to make
            standing.  **Derived from the outcome and never from the
            submission**, which is what makes a rule for a refused creation
            unconstructible rather than merely avoided.  Empty for a pass that
            filed no spending.
    """

    batch: object
    offers: tuple


@dataclass(frozen=True)
class _Answer:
    """What a render has to SAY, beyond the screen itself.

    A parameter object rather than four more arguments, because these four
    ARE one cohesive entity -- *what happened, and what could not* -- which is
    the distinction :func:`~._statement_doors.fragment_door`'s own docstring
    draws against its six independent facts.

    Attributes:
        outcome: The :class:`ReconcilePass` to report, or ``None`` on a plain
            render.
        error: A sentence explaining why nothing was applied at all, or
            ``None``.  Distinct from a refused ITEM: this one means the
            submission never reached the door.
        unacted: The sentence naming the cards the owner OK'd that named no
            act, or ``None``.
        rules: What the RULE door recorded
            (:class:`~app.services.statement_match.StatedRules`), or ``None``
            on every render but the one answering a press of the receipt's own
            offer.  **A separate field from ``outcome`` because it is a
            separate ACT** -- stating where a merchant goes moves no money --
            and the two never travel together: a money pass earns the offer,
            and pressing the offer is the next request.
    """

    outcome: "ReconcilePass | None" = None
    error: "str | None" = None
    unacted: "str | None" = None
    rules: object = None


def _reconcile_context(account, scope, tab, answer: _Answer, opened) -> dict:
    """Assemble what the Reconcile body renders, for the page and the POST.

    ONE builder, because the POST's answer IS the screen: a second assembly
    would let the surface a pass swaps in disagree with the surface a reload
    shows.

    Args:
        account: The owned, attached account.
        scope: The pass to render FROM
            (:class:`~app.services.statement_match.ReviewScope`).  **The
            caller supplies it, and a caller that has just WRITTEN must supply
            a fresh one**: a scope holds the rows and prices as they stood
            before the pass, which is exactly what must not be shown after it.
        tab: Which tab is open (:class:`~app.services.statement_match.Tab`).
        answer: What this render has to say (:class:`_Answer`).
        opened: The bank line whose MATCH pane renders in the document and
            what this request's form holds for it
            (:class:`~app.services.statement_match.OpenedAsk`), or ``None``
            (:func:`~._reconcile_query.opened_ask`).  **A parameter and not a
            read**, for the reason ``tab`` is one: this builder runs after
            the door has committed, so a reader here answers a malformed
            request with a 404 -- or, since plan step ``bank_import:X-gi-2a``,
            a schema refusal -- over a pass that has already been applied.

    Returns:
        The template context.
    """
    show_all = asked_for_everything()
    page = reconcile_page(
        scope,
        # **The route builds the balance pass**, which is the rule every read
        # pass in this project is held to: only a route builds a
        # ``BalanceContext``, and ``bank_agreement`` needs one.
        bank_agreement.bank_agreement(
            account, balance_at.BalanceContext.build(scope.owner_id),
        ),
        tab,
        # **The bound, or the whole record** (plan steps
        # ``bank_import:X-gj-1c`` and ``X-gj-4c-2``).  ONE bound for every
        # bounded arm -- the two settled tabs and, since the developer's
        # ruling **R-JW**, the Skipped tab; the service says which arms
        # read it and the template never asks.  *It said "only the two settled
        # tabs" until that ruling.*
        None if show_all else REGISTER_LIMIT,
        # **WHICH card renders its rows in the document, and what it holds**
        # (plan steps ``bank_import:X-gi-1`` and ``X-gi-2a``, rulings
        # **R-KA**, **R-BI1** and **R-BI3**).
        opened=opened,
    )
    return {
        "account": account,
        "page": page,
        # **The view, so every link on the page can keep it.**  The *show the
        # other N* link and the Undo form each have to say whether this render
        # is the unbounded one, which is the discipline the retired register's
        # own body kept for the same flag: without it an Undo pressed while
        # showing everything answers with the bounded list, and the record
        # collapses under the owner mid-read.
        "show_all": show_all,
        "chips": tuple(
            (chip, _chip_href(account.id, chip)) for chip in page.chips
        ),
        # The picker the NEW-ENVELOPE arm needs.  Loaded here rather than
        # inside the page model because it is not a fact about the statement:
        # it is what any create form on this account offers, and
        # ``list_active_categories`` is the ordering every category picker in
        # the app already shares.
        "categories": list_active_categories(scope.owner_id),
        "outcome": None if answer.outcome is None else answer.outcome.batch,
        "offers": () if answer.outcome is None else answer.outcome.offers,
        "rules": answer.rules,
        "error": answer.error,
        "unacted": answer.unacted,
    }


def _answering(account, tab, unacted, opened):
    """Return this door's own surface, as the callable every arm renders with.

    **A closure rather than a six-argument function**, which is the remedy a
    private helper over the limit takes here: what a door binds once -- the
    account, the tab and what it could not act on -- is bound once, and what
    varies per arm stays the two keywords
    :func:`~._statement_doors.run_statement_fragment_door` calls with.

    **ONE answer for all three outcomes**: a plain render, a receipt after a
    pass, and a refusal are the same page with a different thing to say.

    **A refusal is a designed 400** and carries the marker htmx needs, because
    htmx leaves a 4xx non-swapping: a refusal that renders NOTHING reads as a
    broken button, which is worse than the error it reports.  Which scope each
    arm renders from belongs to
    :func:`~._statement_doors.run_statement_fragment_door`, so "a pass is
    never reported against the state it replaced" is structural rather than
    remembered.

    Args:
        account: The owned, attached account.
        tab: Which tab is open.
        unacted: The sentence naming cards OK'd with no act named, or
            ``None``.
        opened: The bank line whose MATCH pane renders in the document and
            what the form holds for it
            (:class:`~app.services.statement_match.OpenedAsk`), or ``None``.
            Bound here with the other three because it is read BEFORE the
            door for the reason :func:`~._reconcile_query.asked_to_open`
            records -- and since plan step ``bank_import:X-gi-2a`` the
            reading GRADES the body too, so a malformed field for the open
            card is refused before anything is applied rather than raised
            after the commit.

    Returns:
        ``(scope, *, outcome=None, error=None) -> response``.
    """
    def render(scope, *, outcome=None, error=None, rules=None):
        """Render this door's surface from *scope*.

        Args:
            scope: The pass to render from.
            outcome: The :class:`ReconcilePass` to report, or ``None``.
            error: A sentence explaining why nothing was applied, or ``None``.
            rules: What the RULE door recorded, or ``None``.  Set only by
                :func:`state_reconcile_merchant_rules`, which answers with
                this same surface because pressing the receipt's offer leaves
                the owner looking at the page they were already on.

        Returns:
            The rendered body at 200, or the designed-fragment
            ``(body, 400, headers)`` triple when *error* is set.
        """
        body = render_template(
            _BODY,
            **_reconcile_context(
                account, scope, tab,
                _Answer(
                    outcome=outcome, error=error, unacted=unacted,
                    rules=rules,
                ),
                opened,
            ),
        )
        return body if error is None else designed_error(body, 400)
    return render


@accounts_bp.route("/accounts/<int:account_id>/statements/reconcile")
@require_owner
def statement_reconcile(account_id):
    """Render one tab of the Reconcile page.

    Args:
        account_id: The account to reconcile.

    Returns:
        The rendered page, or a 404 when the account is not the caller's, is a
        kind that has no bank statement, or the ``tab`` argument names no tab
        AT ALL -- the security response rule's answer for both "not found" and
        "not yours".  There is no longer a tab this build declines to serve.
    """
    account = load_cash_account_or_404(account_id)
    tab = requested_tab()
    # **No form, so the pane is priced from the proposal the card offers**:
    # a GET carries no body, and the ask says so rather than leaving it to a
    # reader to infer (plan step ``bank_import:X-gi-2a``).
    opened = opened_ask(asked_to_open())
    return render_template(
        "accounts/statement_reconcile.html",
        **_reconcile_context(
            account, ReviewScope.build(current_user.id, account_id), tab,
            _Answer(), opened,
        ),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/reconcile", methods=["POST"],
)
@require_owner
def apply_statement_reconcile(account_id):
    """Apply every card the owner OK'd, and answer with the screen.

    The unit of work is the REQUEST: each item runs inside its own SAVEPOINT
    so a refused one leaves nothing behind while the rest still land, and this
    route commits once -- so a failure outside a designed refusal writes
    nothing at all, which is what makes "nothing was changed" true rather than
    reassuring.

    **It carries ONE act, and it used to carry two.**  The ADD tab had an
    *always, for this merchant* checkbox whose rules were written in this same
    transaction, so that a rule could not survive a rolled-back pass.  Ruling
    **bank_import:R-IB** (2026-08-30) moved that offer onto the RECEIPT, where
    it is asked once per merchant about what the door actually APPLIED: the
    rule was derived from what was OK'd and computed BEFORE this door ran, so
    a per-item refusal rolling back inside its own savepoint left the rule
    standing for a purchase that had not happened.  What this route now does
    with the outcome is EARN the offer (:func:`~app.services.statement_match
    .rules_worth_offering`); pressing it is the next request, through
    :func:`state_reconcile_merchant_rules`.

    Args:
        account_id: The account being reconciled.

    Returns:
        The re-rendered body carrying the outcome, at 200; or the same body
        carrying one refusal sentence at 400, marked as a designed fragment so
        htmx swaps it (:mod:`app.utils.error_fragments`).
    """
    account = load_cash_account_or_404(account_id)
    tab = requested_tab()

    # ONE derivation, built HERE.  Only a route builds a read pass -- the same
    # rule ``BalanceContext`` is held to -- and this one serves three
    # purposes: the door applies against it, either failure arm renders from
    # it, and the receipt's offer reads its destinations off it.
    scope = ReviewScope.build(current_user.id, account_id)

    payload, silent = reconcile_payload(request.form)
    # **The open card is read from THIS body, before the door** (plan step
    # ``bank_import:X-gi-2a``, finding **BI-478**).  The form the press came
    # from holds that card's tickboxes and its consent control, so what it
    # submitted for the card is what the answer prices the pane from -- a
    # refused press comes back with the owner's rows ticked and the
    # difference's acts offered, where it used to come back with the tier's
    # proposal and every tick gone.  Read whether or not the card was OK'd,
    # for :func:`~._reconcile_query.read_match`'s reason.
    opened_line = asked_to_open()
    submission, malformed = (
        (None, None) if opened_line is None
        else read_match(request.form, opened_line)
    )
    render = _answering(
        account, tab,
        None if not silent else _OK_WITH_NO_ACT.format(
            count=len(silent), lines=", ".join(silent),
        ),
        opened_ask(opened_line, submission),
    )
    # **The grader runs before the door**, so a malformed body has written
    # nothing at all.  A malformed body is a pass-level refusal on purpose --
    # it is a fact about the SUBMISSION rather than about an act the owner
    # reviewed, and no browser of ours produces one.  **The open card's own
    # fields are graded under the same rule even where the card was not
    # OK'd**: the batch never reads them then, and a pane priced after the
    # commit from a body the schema would refuse is the door-before-reader
    # trap :mod:`._reconcile_query` records, one field over.  The answer is
    # the sentence over the card's baseline pane, and nothing applied.
    if malformed is not None:
        return render(scope, error=malformed)
    errors = _batch_schema.validate(payload)
    if errors:
        return render(scope, error=refusal_sentence(errors))
    submitted = _batch_schema.load(payload)

    def _apply():
        """Apply the pass against *scope*, inside the caller's transaction.

        Returns:
            The :class:`ReconcilePass` -- what landed, and what that earns the
            right to ask about.
        """
        outcome = apply_reviewed(submitted_batch(submitted), scope)
        return ReconcilePass(
            batch=outcome,
            # **From what the door APPLIED, which is the whole of
            # ``bank_import:R-IB``.**  ``AppliedItem.line_ids`` is documented
            # as a correlation key for exactly this: saying WHICH submitted
            # item an outcome belongs to; and since plan step
            # ``bank_import:X-gx`` ``AppliedItem.merchant`` is the merchant the
            # door filed FOR, read off the row it held locked.  Reading the
            # submission instead is what offered a standing rule for a
            # creation the door had refused; reading the page's derivation
            # for the merchant -- ``review_set``, run here before the door
            # until that step -- is what asked about the merchant the screen
            # had shown rather than the one a re-import had just named and
            # the door had just filed under (finding **BI-495**).
            #
            # **Only a pass that FILES SPENDING can earn a standing-rule
            # offer** (ruling **bank_import:R-GW** -- a merchant answer says
            # where SPENDING goes, so no inflow reaches that loop), so a
            # matches-only or income-only press does not pay for the rule
            # door's accepted sets.
            offers=() if not submitted["creations"] else rules_worth_offering(
                submitted["creations"], outcome.applied, scope,
                _rule_door_accepts(scope),
            ),
        )

    def _record(applied):
        """Log what this pass did, AFTER the commit that made it true.

        Args:
            applied: The :class:`ReconcilePass` the doors produced.
        """
        log_pass_applied(
            _logger, "A Reconcile pass was applied.",
            account_id=account_id,
            item_count=submitted_item_count(submitted),
            outcome=applied.batch,
        )

    return run_statement_fragment_door(
        fragment_door(
            _logger, render=render, scope=scope, account_id=account_id,
            act="apply a statement reconcile pass",
            db_error_message=_DB_ERROR_MESSAGE,
        ),
        _apply,
        _record,
    )


def _rule_door_accepts(scope: ReviewScope) -> RuleDoorAccepts:
    """Return what the RULE door would take, for the receipt's offer.

    **Read here because the service answers no query, and read from the
    producers the door itself validates against** --
    :attr:`~app.services.statement_match.RuleView.template_names` is
    ``offerable_templates``' answer, the very call ``state_rules`` makes, and
    :attr:`~app.services.statement_match.RuleView.active_categories` is
    ``active_category_names``', the predicate (this owner's, ``is_active``)
    that door's own category check applies -- so the offer cannot render a
    press that can never succeed.  **It is not what this pass can file
    into**: an adversarial
    review reproduced both ways the two sets part (a settled envelope from an
    archived template, an ad-hoc envelope under an archived category).

    **One view rather than a review**, since plan step ``bank_import:X-gx``.
    The press ran ``review_set`` -- the whole inbox partition, 0.136 s -- to
    read the merchant section's template tuple and each line's merchant, and
    a category query beside it; the offer reads the merchant off the receipt
    now, so what remains is the rule door's own view, built once here.

    Args:
        scope: The pass, for whose account and owner the door answers.

    Returns:
        The :class:`~app.services.statement_match.RuleDoorAccepts`.
    """
    view = RuleView.build(scope.owner_id, scope.account_id)
    return RuleDoorAccepts(
        template_ids=frozenset(view.template_names),
        category_ids=frozenset(view.active_categories),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/reconcile/merchants",
    methods=["POST"],
)
@require_owner
def state_reconcile_merchant_rules(account_id):
    """Record the standing answers the receipt's offer asked for.

    Plan step ``bank_import:X-gj-1b``, ruling **bank_import:R-IB**.  **It MOVES
    NO MONEY and can move none**, which is why it is its own door and its own
    request: a rule is read to SUGGEST a destination, and the only thing that
    records a purchase is an explicit destination on one specific line.

    **It opens NO door of its own.**  The act is
    :func:`~._statement_rules.record_submitted_rules`, which
    :mod:`.statement_merchants` also posts to, reading the same
    :class:`~app.schemas.validation.merchant_rules.MerchantRuleBatchSchema` off
    the same field names.  Two surfaces, one grader, one writer -- so a rule
    stated from the Reconcile receipt cannot be validated differently from the
    identical rule stated from the merchants list, which is what a second door
    here would have made possible.  What differs is only the SURFACE each
    answers with, which is exactly the split that module exists for.
    *It said THREE surfaces -- the review queue and the register were the
    other two -- until plan step ``bank_import:X-gi-2`` deleted them.*

    **The offer this answers was earned by a money pass** and named only
    merchants that pass actually filed spending for
    (:func:`~app.services.statement_match.rules_worth_offering`).  Nothing
    holds the two requests together, and nothing needs to: a merchant answer is
    a preference about the FUTURE, so stating one for a purchase recorded a
    minute ago is the same act as stating it a week later from the merchants
    list.

    Args:
        account_id: The account being reconciled.

    Returns:
        The re-rendered body carrying what was recorded, at 200; or the same
        body carrying one refusal sentence at 400, marked as a designed
        fragment so htmx swaps it.
    """
    account = load_cash_account_or_404(account_id)
    tab = requested_tab()

    # ONE derivation, built BEFORE the write and still valid after it, and the
    # argument is CLOSED rather than an enumeration of writers:
    # this door writes exactly one table, ``budget.merchant_rules``,
    # through the ORM and calls no service -- so nothing it can do touches the
    # calendar, the candidates or their prices, and ``review_set`` re-reads the
    # rules themselves.  That is a closed argument over one table rather than
    # an enumeration over an open set of writers.
    scope = ReviewScope.build(current_user.id, account_id)
    # **This door's form is the receipt's offer and carries no card**, so an
    # ``?open=`` on it is priced from the proposal, as the GET is.
    render = _answering(account, tab, None, opened_ask(asked_to_open()))

    outcome = record_submitted_rules(
        request.form, current_user.id, account_id, _logger,
    )
    if outcome.refusal is not None:
        return render(scope, error=outcome.refusal)
    return render(scope, rules=outcome.recorded)


# **POST-only deliberately.**  Giving this route a ``GET`` would enrol
# ``line_id`` in ``test_no_baseline_policy``'s url_map sweep, which grades
# every ``GET`` rule and demands a row per converter it finds.
@accounts_bp.route(
    "/accounts/<int:account_id>/statements/reconcile/line/<int:line_id>/match",
    methods=["POST"],
)
@require_owner
def statement_reconcile_match(account_id, line_id):
    """Answer what one card's MATCH tab offers, and what its ticks come to.

    Plan step ``bank_import:X-gj-1b``.  **It MOVES NO MONEY and writes
    nothing**, which is why it may fire on every tick and on every keystroke
    of the search box.  It runs the accept door's own reads and refusals
    through :func:`~app.services.statement_match.preview_hand_build` and
    renders the answer beside the rows; the only door that writes is
    :func:`apply_statement_reconcile`.

    **It takes the body Apply would send for THIS card**, read through
    :func:`~._reconcile_query.read_match` -- the one reading Apply itself
    uses for the card ``?open=`` names (plan step ``bank_import:X-gi-2a``) --
    so the difference on screen and the difference the door compares against
    are one derivation rather than two that agree by reading.

    **The rows it offers are the line's own pay period, or every unexplained
    row on the account when the owner searches** (:class:`~app.services
    .statement_match.MatchCandidates`).  The developer ruled that shape on
    2026-08-30, on the measurement in that class's own module: the period
    holds the payroll group a card is usually about, and the search is what
    keeps a card payment groupable against paybacks its period does not hold.

    Args:
        account_id: The account being reconciled.
        line_id: The bank line whose card is open.

    Returns:
        The re-rendered MATCH pane at 200; or the same pane carrying one
        refusal sentence at 400, marked as a designed fragment so htmx swaps
        it.
    """
    account = load_cash_account_or_404(account_id)
    scope = ReviewScope.build(current_user.id, account_id)
    review = review_set(scope)

    # **The line is found in the PASS rather than queried for**, so a
    # ``line_id`` naming someone else's line, one another match already
    # claims, or one this pass never offered has no card here and gets a 404.
    # It is a membership test rather than a second ownership check, for the
    # reason the retired workbench's own preselection gave: a check written
    # here would be a second statement of the one ``review_set`` applies.
    #
    # **Asked of ``card_subject`` and never of ``unmatched``**, which is what
    # this read once asked and what made every PROPOSED card's pane a
    # permanent spinner: ``_unexplained`` takes a proposal's line out of
    # ``unmatched`` before that list exists, so 137 of the developer's 137
    # proposal cards answered 404 here.  The set a card is drawn from is the
    # pass's own fact and lives beside the two lists it unions.
    subject = review.card_subject(line_id)
    if subject is None:
        abort(404)

    query = request.form.get(f"q-{line_id}", "")
    submitted, refusal = read_match(request.form, line_id)
    if refusal is not None:
        return designed_error(
            render_template(
                _MATCH_PANE, account=account,
                opened=refused_match(subject),
                refusal=refusal,
            ),
            400,
        )
    # NO event and NO commit.  Nothing was written, and a read pass that
    # logged would put a line in the audit trail for every checkbox on the
    # page.
    return render_template(
        _MATCH_PANE,
        account=account,
        # **ONE producer for a pane two surfaces render** (plan step
        # ``bank_import:X-gi-1``).  The Reconcile PAGE builds the same value
        # for the card ``?open=`` names, so a ticked row cannot be dropped on
        # one path and kept on the other.  What differs is the REACH: this
        # render has the search box, so it opens on the line's own pay period
        # (**R-KA**'s byte argument), where the scriptless one cannot narrow
        # and so is given everything.
        opened=opened_match(
            scope, review,
            MatchAsk(
                subject=subject,
                submitted=submitted,
                query=query,
                reach=MatchReach.THE_PERIOD,
            ),
        ),
        refusal=None,
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/reconcile/release",
    methods=["POST"],
)
@require_owner
def release_from_reconcile(account_id):
    """Undo one applied act and come back to the tab it was pressed on.

    Plan step ``bank_import:X-gj-1c``, rulings **bank_import:R-HU** and
    **R-GY**.  **IT MOVES MONEY** -- releasing an act removes the rows that act
    CREATED (**R-GG**) -- and it opens no door of its own: the act, its three
    refusals and its receipt are
    :func:`~._statement_release.release_and_return`'s, which the import
    receipt also posts to.  Two surfaces, one door, one
    derivation of what the press destroys, so the confirmation a card shows
    cannot promise what the button will not do.  *It said THREE until plan
    step ``bank_import:X-gi-2`` deleted the register.*

    **A plain POST-redirect-GET where everything else on this page swaps.**
    That is the subject rather than an inconsistency, and it is the shape the
    import receipt already uses: this names ONE act and either does it or
    refuses it, so a flash carries the whole answer -- where Apply reports
    per-item outcomes no flash can hold.  It also keeps the Undo a `form`,
    which is why an act card is not rendered inside the Apply form: a form
    cannot nest in a form, and a `summary` may not hold one at all.

    **It comes back to the VIEW, not merely to the page.**  Which tab is open
    and whether the settled bound is lifted are both read here through the
    page's own two readers, so an undo pressed on Filed by rules while showing
    every act answers with Filed by rules showing every act.  Redirecting to
    the bare URL would drop the reader onto the inbox, which is the defect that
    made ``release_and_return`` take a target at all.

    Args:
        account_id: The account being reconciled.

    Returns:
        A redirect back to the tab and view the control was pressed on,
        carrying the receipt or the refusal.
    """
    account = load_cash_account_or_404(account_id)
    return release_and_return(
        account, "accounts.statement_reconcile",
        tab=requested_tab().value,
        # ``url_for`` drops a ``None`` argument, so the ordinary render
        # redirects to the plain URL rather than to one carrying ``all=``.
        all=1 if asked_for_everything() else None,
    )


def _unskip_report(line_id: int) -> "tuple[str, str]":
    """Return the flash for one undone skip: what came back.

    **It names no figure and no removal**, which is the whole difference from
    :func:`~._statement_release._release_report` one act over: undoing a match
    destroys rows that match created and owes the owner their count and their
    money, and undoing a skip destroys a DECISION and takes nothing back.  A
    receipt reporting `$0.00` removed would be inventing a fact about money on
    a screen whose one job is to be trusted about money.

    **It says the difference is unchanged**, and that is the sentence this act
    is easiest to misread: skipping never closed the gap between the books and
    the bank (:mod:`~app.services.statement_match._skipping`), so undoing one
    cannot re-open it.

    **It names NO row id.**  ``budget.bank_statement_lines.id`` appears nowhere
    the owner can see -- a card shows the merchant, the day and the bank's raw
    words -- so a receipt quoting one would be the only figure on this screen
    naming nothing on it, and :func:`~._statement_release._release_report`
    deliberately quotes none either.  *A first version interpolated it*, which
    adversarial review found was the sole flash in ``app/routes/`` to do so.
    The line identifies itself by being back in the inbox, which is where this
    sentence sends the reader.

    Args:
        line_id: The bank line that is unexplained again, which
            :func:`~app.services.statement_match.unskip_line` returns.
            **Taken because the callback contract passes it and deleted
            because this receipt does not print it**: the door returns the
            line for the SERVICE's own rule -- one act, one named subject --
            and ``app/`` has no other reader.  *An earlier version claimed
            the caller "is told" which line it freed, which is circular over
            a value the next line discards.*

    Returns:
        ``(message, category)``.
    """
    del line_id
    return (
        "Skip undone.  That statement line is waiting to be explained again; "
        "nothing else changed, because skipping had recorded no money and "
        "closed no difference between your books and your bank.",
        "info",
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/reconcile/unskip",
    methods=["POST"],
)
@require_owner
def unskip_from_reconcile(account_id):
    """Undo one recorded skip and come back to the Skipped tab.

    Plan step ``bank_import:X-gj-4c-2``, rulings **bank_import:R-JG** and
    **R-GY**.  **IT MOVES NO MONEY AND CAN MOVE NONE** -- the only table
    :func:`~app.services.statement_match.unskip_line` touches is
    ``budget.statement_line_skips``, which holds no figure -- which is what
    separates it from :func:`release_from_reconcile` beside it, whose act
    destroys rows and owes a confirmation naming their money.  What this
    restores is the QUESTION: the line is unexplained again and the inbox asks
    about it.

    **A plain POST-redirect-GET**, which is the shape the other Undo on this
    page already takes and for its stated reason: this names ONE act and either
    does it or refuses it, so a flash carries the whole answer -- where Apply
    reports per-item outcomes no flash can hold.  It also keeps the control a
    ``form``, which is why the Skipped tab renders outside the Apply form: a
    form cannot nest in a form.

    **It does NOT go through**
    :func:`~._statement_release.release_and_return`, and the reason is the
    subject rather than the shape.  That helper exists because ONE act -- the
    release of a match -- is offered from three surfaces and its refusal story
    and receipt must be identical on all of them.  This is a different act on a
    different table with a different receipt, offered from exactly one surface,
    so folding the two would be a shared helper for two things that only look
    alike, which is what ``CLAUDE.md`` rule 13 refuses.

    **It comes back to the VIEW, not merely to the page**, which is
    :func:`release_from_reconcile`'s own rule one act over: which tab is open
    and whether the bound is lifted are both read here through the page's own
    two readers, so an undo pressed on the Skipped tab while showing every
    skip answers with the Skipped tab showing every skip.  *An earlier version
    of this docstring argued the opposite* -- that the tab needed no bound, so
    ``all`` was an argument this page could not honour -- and the developer
    ruled at **R-JW** that it takes ruling **R-GX**'s shape after all.

    Args:
        account_id: The account being reconciled.

    Returns:
        A redirect back to the tab the control was pressed on, carrying the
        receipt or the refusal.  A 404 when the account is not the caller's or
        is a kind that has no bank statement -- the security response rule's
        answer for both "not found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)
    target = url_for(
        "accounts.statement_reconcile",
        account_id=account.id, tab=requested_tab().value,
        # ``url_for`` drops a ``None`` argument, so the ordinary render
        # redirects to the plain URL rather than to one carrying ``all=``.
        all=1 if asked_for_everything() else None,
    )

    return run_one_id_door(
        _unskip_schema, "skip_id",
        StatementDoorContext(
            logger=_logger,
            refusal=ValidationError,
            log_message="user_id=%d failed to undo a skip on account %d",
            log_args=(current_user.id, account.id),
            flash_message=(
                "Something went wrong undoing that skip.  Nothing was "
                "changed."
            ),
            target=target,
        ),
        lambda skip_id: unskip_line(skip_id, current_user.id, account.id),
        _unskip_report,
    )
