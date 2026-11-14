"""The recurring transfer funding an account, and what that DEFINITION says.

A single-responsibility leaf helper for the one query three surfaces share --
"does an active recurring transfer template pay INTO this account, and if so
which one?" -- and for the facts read off the template it returns.  It
imports no service at all -- ``db`` and two models -- so the graph stays
acyclic: every consumer of THIS module imports it, and none of them is
reachable from there.  The loan and investment
dashboards use the query to decide whether to show the
set-up-a-recurring-payment prompt, and the loan recurrence-sync uses it to
find the rule whose OPENING bound it re-derives from the loan's contract
(:func:`~app.services.loan_recurrence_sync.sync_loan_payment_start`).
Centralising it keeps those surfaces from drifting on what counts as an
account's recurring funding transfer.  **Since plan step**
``pay_calendar:C9`` **the balance seam's cash fold reads the same set from
either side of an account** (:func:`active_recurring_transfer_templates_on`),
to estimate its transfers past the last saved paycheck; both loaders filter
the one statement of an active recurring transfer
(:func:`_active_recurring_transfers`).

**The loan-payment SETTINGS read moved here at plan step R7d-a**, from
``loan_payment_service``.  It is a read OF this module's own subject -- the
mode a payment is in and the standing extra it carries -- and it was three
call sites away from the query that finds the template it reads.  That module
was at pylint's 1000-line ceiling exactly, so the move is also what stops the
next fact about a definition being paid for by a ``too-many-lines`` disable; a
module's line count going over is a statement that it holds more than one
subject, and this was the second one.  **Its LOAN-LEVEL siblings went at plan
step R7d-g-3** -- ``standing_payment`` / ``StandingPayment`` (the oldest
definition and ITS extra, bundled) and ``loan_standing_extra`` (that extra
alone): each answered a loan-level question by picking ONE definition, the
tie-break ruling **R-R35** wants deleted rather than answered, and the two
readers that priced money off them (the balance seam's resolver and the loan
page's payoff composer, ``loan_resolver.compute_payoff_scenarios``) take no
extra at all now (ruling **R-R88**, which re-ruled R-R83's seam clause at R7d-g-3): a generated row
carries its own definition's extra through amount rule 4, and what no row
covers is priced from every definition's own occurrences by the forward plan.
The settings row is read per DEFINITION, through :func:`loan_payment_config`.

What is deliberately NOT here is how a MATERIALISED row is priced
(``cash_ledger.LoanPricing``): that needs the loan resolved, its rate
periods and its escrow history, which is the AMOUNT MODEL's work -- rule 4 --
and not a read of a definition.  *That rule's producer lived in
``loan_payment_service`` until plan step X-au-g-2a moved it into
``cash_ledger``; this sentence said "the loan seam's work" and now names the
tier that actually owns it.*  **The definition's half of that rule lived here
too, as ``standing_installment_cash``, until plan step R16-b-2 deleted it**
(ruling **R-R67**): it was a copy of the amount model's own arms, measured a
cent apart from them on a loan's last installment (finding **REC-517**), and
an estimate is priced through those arms now
(:func:`app.services.cash_ledger.definition_cash`).
"""

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import or_
from sqlalchemy.orm import Query, joinedload

from app.extensions import db
from app.models.account import Account
from app.models.transfer_template import TransferTemplate

if TYPE_CHECKING:
    # Type-only, both: a ``TransactionTemplate`` names only
    # ``destination_account``'s parameter here (it carries no
    # ``to_account_id`` at all, which is what the ``getattr`` there is for),
    # and ``recurring_definition`` reaches this module at runtime through
    # ``loan_recurrence_sync``, so the edge back is a forward reference and
    # nothing more.
    from app.models.transaction_template import TransactionTemplate
    from app.services.recurring_definition import UnsavedDefinition


def destination_account(
    template: "TransferTemplate | TransactionTemplate | UnsavedDefinition",
) -> Account | None:
    """Return the account *template* pays into, or ``None`` when it pays into none.

    **The COLUMN, then a lookup -- never ``template.to_account``**, and an
    adversarial review of plan step R7d-b measured why.  That relationship is
    ``lazy="joined"``, which loads it with the template and then does NOT
    refresh it when the FK column is written: measured on SQLAlchemy 2.0.49,
    a ``setattr(template, "to_account_id", other)`` leaves ``to_account``
    pointing at the OLD account through the following ``flush()`` and only
    re-loads at ``commit()``.  ``routes/transfers/templates.py`` writes
    exactly that -- ``to_account_id`` is in ``_TEMPLATE_UPDATE_FIELDS`` and
    is assigned by ``setattr`` -- and then REGENERATES before committing, so
    a resolver reading the relationship would bound the new destination's
    rows by the OLD loan's payoff.  A pending template is the second state:
    its ``to_account_id`` is set and its ``to_account`` is still ``None``.
    ``db.session.get`` costs nothing when the row is already in the identity
    map, which is the case the joined load creates anyway.

    ONE spelling, shared by
    :func:`app.services.loan_recurrence_sync.loan_payment_window` and
    :func:`app.services.balance_at.is_standing_loan_payment` (plan step R7d-f;
    the first cut carried the read twice in one module and an adversarial
    review named it).  **It lived in ``loan_recurrence_sync`` as a private
    until plan step R16-b-2** moved the identity it serves into the balance
    seam (ruling **R-R70**): that module imports the seam, so the seam could
    not reach the read there, and a definition's destination is a fact about
    the DEFINITION -- this module's subject -- rather than about a loan's
    window.

    Args:
        template: A ``TransferTemplate``, or a ``TransactionTemplate``, which
            carries no ``to_account_id`` at all -- ``getattr`` on the FK
            column is what keeps both readers kind-agnostic.

    Returns:
        The destination :class:`~app.models.account.Account`, or ``None`` when
        the template pays into no account or names one not yet flushed.  The
        second is unreachable for a persisted definition -- ``to_account_id``
        is NOT NULL under an ``ON DELETE RESTRICT`` foreign key -- and a
        pending one generates nothing either way.
    """
    account_id = getattr(template, "to_account_id", None)
    if account_id is None:
        return None
    return db.session.get(Account, account_id)


def _active_recurring_transfers(user_id: int) -> Query[TransferTemplate]:
    """Return the query for *user_id*'s active recurring transfer templates.

    The ONE statement of "an active recurring transfer" and of what its
    readers price it from, filtered by side by each public loader below
    (:func:`active_recurring_transfer_templates`, the transfers INTO an
    account; :func:`active_recurring_transfer_templates_on`, those on either
    side of it), so the two cannot come to disagree about which definitions
    count.

    The 1:1 ``settings`` row and the price SERIES are eager-loaded on every
    row -- see the comments on the options for why.

    Args:
        user_id: The owning user (scopes the query -- ownership is established
            by the caller's chokepoint).

    Returns:
        An unexecuted query, ordered by id.
    """
    return (
        db.session.query(TransferTemplate)
        .options(
            joinedload(TransferTemplate.settings),
            # The price SERIES, because a caller that resolves it per
            # occurrence (the balance seam's ESTIMATED tier through
            # ``cash_ledger.definition_cash``, ~300 times for a 30-year loan)
            # would otherwise take a lazy load mid-fold.  One collection per
            # template, and the settings row beside it is loaded the same way
            # for the same reason.
            joinedload(TransferTemplate.amount_versions),
        )
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.is_active.is_(True),
            # "Carries a recurrence rule", as an EXISTS rather than a NOT NULL
            # column test: the owning FK moved onto the rule at plan step
            # R-F6, so what used to be ``recurrence_rule_id IS NOT NULL`` here
            # is now a row on the other side.
            #
            # ``uq_recurrence_rules_transfer_template_id`` covers the join
            # column, so the subquery is index-ABLE -- which is a statement
            # about what happens as the table grows, not about today.  Measured
            # on a production clone (6 transfer templates, 43 rules), the
            # planner chooses a nested loop over sequential scans and is right
            # to: an index probe costs more than reading two tiny tables.  The
            # earlier comment here claimed the probe as fact and an adversarial
            # review measured otherwise.
            TransferTemplate.recurrence_rule.has(),
        )
        # **ORDERED, because the first row of an unordered query is whichever
        # row the planner hands back** -- and from plan step R7d-a to R16-b-2
        # that answer decided how the loan's whole forward plan was PRICED.
        # It prices nothing now (the tier sums every row here, finding
        # **D47**), but it still decides the loan-payment identity the form
        # locks on and the opening-bound sync writes for, and two renders in
        # one session must not disagree about it.  The oldest definition wins,
        # which is stable under later edits.
        .order_by(TransferTemplate.id)
    )


def active_recurring_transfer_templates(
    account_id: int, user_id: int,
) -> list[TransferTemplate]:
    """Return EVERY active recurring transfer template paying INTO *account_id*.

    The active (``is_active``) :class:`TransferTemplate` rows owned by
    *user_id* whose destination is *account_id* and which carry a recurrence
    rule (a ``budget.recurrence_rules`` row names each), oldest first.  **The
    set, not one of it** (plan step **R16-b-2**, ruling **R-R35**): every
    recurring transfer into a loan is a payment against it, and the balance
    seam's ESTIMATED tier sums each one's occurrences on its own cadence --
    so the question this answers is "which definitions pay in here", where
    :func:`active_recurring_transfer_template` below answers the narrower
    "which ONE is the standing payment" for the opening-bound sync alone.
    That function is this one's first element, so the filter is stated once.
    **Since plan step R7d-g-3 the loan dashboard reads THIS set too**: its
    payment card offers extra-principal and track controls per definition
    (ruling **R-R83**), and its two settings doors admit a template only if
    it is in this set for the loan the URL names.

    Args:
        account_id: The destination account (a loan or investment account).
        user_id: The owning user (scopes the query -- ownership is established
            by the caller's chokepoint).

    Returns:
        The active recurring :class:`TransferTemplate` rows, ascending by id;
        ``[]`` when the account has no recurring funding transfer.
    """
    return (
        _active_recurring_transfers(user_id)
        .filter(TransferTemplate.to_account_id == account_id)
        .all()
    )


def active_recurring_transfer_templates_on(
    account_id: int, user_id: int,
) -> list[TransferTemplate]:
    """Return every active recurring transfer template *account_id* is on, either side.

    The definitions a generate pass writes transfer rows from that move money
    out of or into the account: the balance seam's cash fold estimates each
    one's occurrences past the last saved paycheck (plan step
    ``pay_calendar:C9``, ruling plan#3930), the transfer twin of the rows
    :func:`app.services.cash_ledger.planned_cash_rows` loads on either side.

    Args:
        account_id: The account a template moves money out of or into.
        user_id: The owning user (scopes the query -- ownership is established
            by the caller's chokepoint).

    Returns:
        The active recurring :class:`TransferTemplate` rows from or to the
        account, ascending by id; ``[]`` when there are none.
    """
    return (
        _active_recurring_transfers(user_id)
        .filter(
            or_(
                TransferTemplate.from_account_id == account_id,
                TransferTemplate.to_account_id == account_id,
            )
        )
        .all()
    )


def active_recurring_transfer_template(
    account_id: int, user_id: int,
) -> TransferTemplate | None:
    """Return the active recurring transfer template paying INTO *account_id*.

    The OLDEST of :func:`active_recurring_transfer_templates`, and the
    ordering is load-bearing rather than tidy -- see the comment on it there.
    More than one recurring transfer into a single account is a state
    ``routes/loan/payment_transfer.py`` handles rather than refusing, so the
    set is not guaranteed to hold one row.  ``None`` when the account has no
    recurring funding transfer.

    **Since plan step R16-b-2 this row PRICES nothing** -- the ESTIMATED tier
    sums every definition (:func:`active_recurring_transfer_templates`) --
    and what it still decides is the loan-payment IDENTITY the form locks on
    and the opening-bound sync writes for (ruling **R-R81**: the standing
    payment's start is the loan's contract fact, a second transfer's is its
    owner's).  **Its readers since plan step R7d-g-3 are exactly two**:
    :func:`~app.services.loan_recurrence_sync.sync_loan_payment_start`, the
    one writer of that bound, and the investment dashboard's "is a
    contribution set up" test (an existence read, where any member of the
    set would do).  The balance seam reads the same identity off the FIRST
    element of the plural it already holds
    (:func:`app.services.balance_at.is_standing_loan_payment`) rather than
    through this function.  The three loan-side readers that needed ONE row
    -- the dashboard's extra-principal prefill and the two routes that MUTATE
    a settings row -- went at R7d-g-3 (plan ledger row **D49**, ruling
    **R-R83**): the card is per definition and the doors take a template id.
    Ruling **R-R35** wants the search deleted rather than answered; the
    opening-bound sync is what keeps it.

    Args:
        account_id: The destination account (a loan or investment account).
        user_id: The owning user (scopes the query -- ownership is established
            by the caller's chokepoint).

    Returns:
        The oldest active recurring :class:`TransferTemplate`, or ``None``.
    """
    templates = active_recurring_transfer_templates(account_id, user_id)
    return templates[0] if templates else None


def loan_payment_config(template: TransferTemplate) -> tuple[bool, Decimal]:
    """Return ``(derive_from_loan, extra_principal)`` for a transfer template.

    The single accessor for a recurring transfer's loan-payment settings
    (:class:`~app.models.loan_payment_settings.LoanPaymentSettings`, decision B),
    which live in a 1:1 table rather than on the generic template.  A template
    with NO settings row is not a loan payment: ``derive_from_loan`` defaults
    ``False`` and ``extra_principal`` ``Decimal("0.00")``, so the live-derive and
    overpayment machinery stays dormant for every investment contribution and
    generic transfer.  The ``settings`` relationship must already be loaded by
    the caller (the readers ``joinedload`` it) so this stays a pure in-memory
    read with no N+1.

    **It became PUBLIC at plan step X-au-b**, and the alternative was a second
    copy.  The amount resolver
    (:mod:`app.services.cash_ledger._amount_source`) has to know which MODE a
    loan payment is in before it can price it -- a derive-mode payment resolves
    from the loan, a manual one from its definition plus this ``extra`` -- and
    reading ``template.settings`` there would be a second spelling of the
    row-absent defaults this function exists to state once.  An adversarial
    review found the resolver answering a manual payment two different ways for
    want of exactly this.

    Args:
        template: The :class:`~app.models.transfer_template.TransferTemplate`
            whose loan-payment settings to read.

    Returns:
        ``(derive_from_loan, extra_principal)`` -- the settings row's values, or
        ``(False, Decimal("0.00"))`` when the template has no settings row.
    """
    settings = template.settings
    if settings is None:
        return False, Decimal("0.00")
    return settings.derive_from_loan, Decimal(str(settings.extra_principal))


def tracking_definition(
    definitions: list[TransferTemplate],
) -> TransferTemplate | None:
    """Return the definition among *definitions* that TRACKS the loan, or ``None``.

    The oldest whose settings row says ``derive_from_loan`` -- the payment
    whose every occurrence is priced at the loan's full contractual
    installment (amount rule 4's derive arm).  ONE such definition is the
    state a loan can sensibly be in: a second would pay the contract twice a
    month, the shape ruling **R-R83**'s worked example named as the defect
    (`$531.94 + $531.94`), so the loan dashboard's track door REFUSES to make
    a second one and its card offers no Track control while one exists
    (developer, 2026-09-14, at plan step R7d-g-3).  Both read this function,
    over the list they already hold, so the door and the card cannot
    disagree about which definition that is.  A loan holding two already --
    a state no door can create now, and none exists on production -- answers
    its oldest, and both then show as tracking with nothing offered.

    Args:
        definitions: The loan's active recurring transfers, oldest first
            (:func:`active_recurring_transfer_templates`), settings rows
            loaded.

    Returns:
        The tracking :class:`TransferTemplate`, or ``None`` when every
        definition is a fixed amount.
    """
    for template in definitions:
        tracks_loan, _extra = loan_payment_config(template)
        if tracks_loan:
            return template
    return None
