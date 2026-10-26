"""
Shekel Budget App -- WHICH RELATIONSHIPS pricing a row walks.

Four functions, and they exist so that the eager load the amount model needs
is stated ONCE rather than copied into every loader that feeds it.
:mod:`app.services.cash_ledger._amount_source` owns the five rules; this owns
the relationship graph those rules traverse, so a rule that starts reading a new
relationship adds it here in the same edit and every routed loader gets it.

**It is a ``utils`` leaf rather than a member of the cash-ledger package, and
the reason is a tier rather than a preference.**  ``app.services.loan_loaders``
is one of the loan TERM primitives the cash ledger itself imports
(``cash_ledger._loan_installment`` -> ``load_loan_params`` /
``load_rate_changes`` / ``loan_payment_due_date``;
``cash_ledger._loan_pricing`` -> ``load_escrow_lines``), and plan step X-au-g-2a
moved rule 4's producer DOWN precisely so that arrow runs ONE way.  Its two
payment producers read ``budget.transfers`` and take their callers'
transfer-rooted loads -- :func:`transfer_pricing_load_options` for a caller
that prices the projected half -- plus :func:`transfer_period_load_option`
for their own sort key, and they cannot ask the cash ledger for either, at
module level or at call time, because ``cyclic-import`` (R0401) traces
function-level imports too.  *The loader that first needed this was the
shadow-income query, whose rows were all DERIVED transfer shadows; plan step
balance:X-bi-6-4b deleted it when the settled half moved onto transfers.*  A leaf both tiers
can reach is the same shape :mod:`app.services.row_valuation` already is, and
this one is smaller: it names SQLAlchemy and four models and no service at all.

:mod:`app.services.cash_ledger` re-exports it, so a caller ABOVE the amount
model asks the model for its own load and only the tiers beneath it name this
module directly.
"""

from sqlalchemy.orm import selectinload

from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate


def period_load_option():
    """Return the loader option for a row's PAY PERIOD -- one spelling.

    Its one asker is :func:`pricing_load_options`, which applies it -- for
    amount rule 4's DERIVE arm until plan step X-au-f-2 moved that read onto
    the PARENT transfer's period, and since then for the reason that set's own
    entry states (finding **BAL-477**).  The loan partition's SETTLED half
    asked it too, for its sort key, until plan step balance:X-bi-6-4b moved
    that half onto ``budget.transfers``; both halves of
    :func:`app.services.loan_loaders.income_legs` ask
    :func:`transfer_period_load_option` since.  The option is stated HERE
    rather than written as ``selectinload(Transaction.pay_period)`` at each
    asker, and the reason is a failure mode rather than tidiness: two options
    naming one relationship path in one query merge when they are identical,
    and are a hard SQLAlchemy error when their strategies differ -- the
    history of ``loan_loaders.query_shadow_income`` (deleted at plan step
    balance:X-bi-6-4b) records that exact failure being how a duplicate load
    was found.  One producer makes the strategies unable to differ.

    Returns:
        The ``selectinload`` option for :attr:`Transaction.pay_period`.
    """
    return selectinload(Transaction.pay_period)


def transfer_period_load_option():
    """Return the loader option for a TRANSFER's pay period -- one spelling.

    The :class:`~app.models.transfer.Transfer`-rooted twin of
    :func:`period_load_option`, for the same reason (plan step X-bi-6a):
    :func:`app.services.loan_loaders.projected_income_legs` sorts on the
    parent's period start and adds this regardless of what its caller asked
    for, and :func:`transfer_pricing_load_options` names the same path for
    rule 4's DERIVE arm.  Two options naming one path with different
    strategies is a hard SQLAlchemy error; one producer makes them unable to
    differ.

    Returns:
        The ``selectinload`` option for :attr:`Transfer.pay_period`.
    """
    return selectinload(Transfer.pay_period)


def pricing_load_options() -> tuple:
    """Return the loader options a caller resolving MANY rows should apply.

    Every relationship below is ``lazy="select"``, so a loader that omits them
    makes the resolver issue queries per row.

    Six chains, covering NINE relationships.  An adversarial review counted
    seven at plan step X-au-b after a first draft named two; the eighth was
    found by that step's own query-count control, which is worth stating
    because a census is exactly the kind of claim that reads as complete --
    **and a first draft of THIS sentence said TEN**, having added one chain and
    counted two relationships for it.  A census miscounted inside a paragraph
    about censuses reading as complete; caught by an adversarial review of
    plan step X-au-f, which listed the nine.
    **The tenth arrived at plan step X-au-f-2**, when ruling **R-BAL10** moved
    a loan payment's answer onto the PARENT: the derive arm dates its
    installment from the TRANSFER's two columns now, so the chain it walks is
    ``Transaction.transfer -> pay_period`` where it was the shadow's own
    period.  *Since plan step X-bi-6a the tuple holds FOUR entries: the three
    ``Transaction.transfer -> ...`` chains are one entry composing
    :func:`transfer_pricing_load_options`, so the count of entries is no
    longer the count of chains, and the loader elements the tuple expands to
    are NINE (five under ``Transaction.transfer``, three under
    ``Transaction.template``, and the period), measured by walking the
    composed options rather than counted from this list.*  The list:

    * ``Transaction.template`` -> ``salary_profiles`` -- amount rule 2's
      refinement (``_amount_source._rule_within_definition``) asks whether an
      ACTIVE profile names the definition;
    * ``Transaction.template`` -> ``amount_versions`` -- rule 3 resolves the
      definition's price series as of the row's own due date;
    * ``Transaction.transfer`` -> ``template`` -> ``settings`` -- rule 4's
      refinement (``_amount_source._is_loan_payment``) asks whether a loan
      payment's settings row hangs off the parent's definition;
    * ``Transaction.transfer`` -> ``template`` -> ``amount_versions`` -- rule 5
      and rule 4's MANUAL arm both price through
      ``_amount_source.resolve_transfer_amount``;
    * ``Transaction.transfer`` -> ``pay_period`` -- rule 4's DERIVE arm dates
      the installment it prices, and
      ``loan_loaders.installment_for`` takes the period start EAGERLY rather
      than only on its no-``due_date`` fallback, so the relationship is read on
      every call.  **It was the SHADOW's own period until plan step X-au-f-2**,
      where ruling R-BAL10 moved the answer to the parent; the parent's period
      is the same value (Transfer Invariant 3) read from the row that now
      carries the rule.
    * ``Transaction.pay_period`` -- **no amount RULE reads this since plan step
      X-au-f-2**, and it is kept rather than dropped because the drop is a
      different question with a different blast radius: five batch loaders
      apply this set and their OTHER reads of the relationship have not been
      censused, so deleting it here could turn one of them into an N+1 in
      silence.  Filed as **BAL-477** against plan step X-bm, which owns this
      set's batch callers.  **It was missing from the first draft
      of this set**, and the control in
      ``test_a_transfer_shadow_is_derived.test_the_declaration
      .TestTheAmountModelsOwnEagerLoad`` is what found it: three
      ``budget.pay_periods`` reads against an assertion of zero.  Before that
      step the obligation was one loader's --
      ``loan_loaders.query_shadow_income`` eager-loaded it, and every caller of
      that derivation came through there -- and declaring every shadow derived
      is what spread it to the grid and the cash fold.  *That loader carried no
      such obligation after plan step balance:X-bl-2a made the load its
      caller's statement, and plan step balance:X-bi-6-4b deleted it; the
      path is shared through :func:`period_load_option` instead, which is
      what stops the two spellings differing.*

    **The per-ROW chains are a true N+1 and the per-DEFINITION ones are not**: a
    template's collections are identity-mapped, so 44 templates served 452 rows
    on the 2026-08-12 production clone, while ``Transaction.transfer`` is one
    query per shadow.  That asymmetry is why this stopped being a paragraph of
    advice at plan step X-au-g-2c-2.  Before that step a shadow OWNED its
    figure, so ``amount_rule`` answered ``OWN`` off the column and never touched
    the relationship; a shadow is DERIVED now -- **350 of them on production,
    measured 2026-09-01 at stamp ``a4c6f1d92b73``** -- so every surface that
    loads one walks to its parent, and a loader that forgets pays per row.

    ``selectinload`` throughout rather than ``joinedload``: three of the chains
    end in a COLLECTION, and a joined load over two collections multiplies the
    row count.

    **It is deliberately NOT conditional on which rows a caller holds.**  A
    loader does not know which rule will price each row -- that is the amount
    model's own dispatch -- and an option for a relationship no row in the set
    uses costs one empty ``SELECT ... IN ()``-shaped query at most, where a
    missing one costs a query per row.

    Returns:
        A tuple of SQLAlchemy loader options, splatted into ``Query.options``.
    """
    return (
        selectinload(Transaction.template).selectinload(
            TransactionTemplate.salary_profiles,
        ),
        selectinload(Transaction.template).selectinload(
            TransactionTemplate.amount_versions,
        ),
        # The three ``Transaction.transfer -> ...`` chains are the PARENT's own
        # pricing graph, stated once in :func:`transfer_pricing_load_options`
        # and composed under the shadow's link here (plan step X-bi-6a), so a
        # rule that starts reading a new relationship off a transfer adds it
        # in one place and both a shadow and a planned leg get it.
        selectinload(Transaction.transfer).options(
            *transfer_pricing_load_options(),
        ),
        period_load_option(),
    )


def transfer_pricing_load_options() -> tuple:
    """Return the loader options a caller resolving MANY transfers should apply.

    The TRANSFER-rooted half of :func:`pricing_load_options` (plan step
    **X-bi-6a**), split out because the parent is now loaded in its own right:
    a still-projected transfer's two legs are DERIVED from the parent row
    rather than read off its shadow rows (ruling **R-BAL13**), so the loaders
    that feed a cash fold, a loan's forward plan and the investment
    contribution feeds select from ``budget.transfers`` and price each row
    through :func:`app.services.cash_ledger.resolve_transfer_amount`.  What
    that resolver walks, per row:

    * ``Transfer.template`` -> ``settings`` -- rule 4's refinement asks whether
      a loan payment's settings row hangs off the definition;
    * ``Transfer.template`` -> ``amount_versions`` -- rule 3 and rule 4's
      MANUAL arm price the definition's series as of the transfer's due date;
    * ``Transfer.pay_period`` -- rule 4's DERIVE arm dates the installment from
      the parent's period start
      (:func:`app.services.loan_loaders.installment_for` reads it eagerly, not
      only on its no-``due_date`` fallback), and the loan partition sorts on
      it.

    ONE spelling: :func:`pricing_load_options` composes exactly this tuple under
    ``Transaction.transfer`` for a shadow that still reaches its parent, so the
    two paths to a transfer's price cannot name different relationships.

    Returns:
        A tuple of SQLAlchemy loader options, splatted into ``Query.options``
        on a select rooted at :class:`~app.models.transfer.Transfer`.
    """
    return (
        selectinload(Transfer.template).selectinload(TransferTemplate.settings),
        selectinload(Transfer.template).selectinload(
            TransferTemplate.amount_versions,
        ),
        transfer_period_load_option(),
    )


def settlement_load_options() -> tuple:
    """Return the load a reader of a SETTLED row's RECORD needs: its entries.

    **The record is the row's entries** (plan step ``balance:X-bi-4b-1``,
    ruling **R-BAL80**): ``row_valuation.settled_figure`` sums them for every
    settled row, and ``status_seam.recorded_settlement`` / ``honoured_correction``
    read the covering movement among them.  A settled-only reader -- the loan
    ledger's event stream and its confirmed history, the asset and investment
    contribution passes -- prices nothing and so takes no pricing load, but it
    values every row through the record, so it takes THIS; through ``X-bi-4a``
    those readers stated ``options=()`` because the record was the row's own
    two columns, and the first cut of 4b-1 left them so, at one
    ``transaction_entries`` SELECT per settled payment on the loan walk (six
    on the production Mortgage, measured by that leaf's adversarial review).
    :func:`valuation_load_options` composes it, so the entries load has ONE
    spelling.

    Returns:
        A tuple of SQLAlchemy loader options, splatted into ``Query.options``
        or handed to a loader's ``options=``.
    """
    return (selectinload(Transaction.entries),)


def valuation_load_options() -> tuple:
    """Return the loads a CONTRIBUTION pass needs: pricing, plus the entries.

    :func:`pricing_load_options` covers what the five amount RULES read -- what
    a row's amount IS.  A pass that asks what a row is WORTH reads one more
    relationship on top: ``Transaction.entries``, for the envelope reservation
    (``cash_ledger._amounts._entry_aware_amount``) and for every settled row's
    record (``row_valuation.settled_figure``, which sums them since plan step
    ``balance:X-bi-4b-1``; a ``purchases``-basis close's before it) --
    :func:`settlement_load_options`, the one spelling of that load.

    **It is a separate function rather than more of the one above, because the
    two questions have two answers** -- the same split
    :mod:`app.services.cash_ledger._amount_source` and
    :mod:`app.services.cash_ledger._amounts` already are.  A caller that only
    resolves amounts (the grid's quick-edit fragment, a single-row render)
    should not pay for a purchase load it never reads.

    **It exists because two loaders came to state the same set** (plan step
    X-au-g-2c-2): ``routes/grid/page.py``'s window loader and
    ``cash_ledger._facts``'s fold loader both want entries beside the pricing
    relationships, and spelling that twice is what ``duplicate-code`` reported
    the moment the second one gained the pricing half.  The gate was right --
    two loaders feeding one valuation is one decision, not two.

    Returns:
        A tuple of SQLAlchemy loader options, splatted into ``Query.options``.
    """
    return (*settlement_load_options(), *pricing_load_options())
