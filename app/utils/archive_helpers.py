"""
Shekel Budget App -- Archive and Delete History Helpers

Provides history-detection functions used by the unified delete/archive
pattern across transaction templates, transfer templates, accounts,
and categories.  Each function answers: "Does this entity have settled
history that prevents permanent deletion?"

These functions are pure queries -- they do not perform mutations.

The transaction/transfer template predicates filter on the semantic
``Status.is_settled`` boolean column (audit finding CRIT-05 / E-22):
enumerating ``[Paid, Settled]`` by name or ID silently missed Received
-- the status assigned to every income paycheck on mark-done -- and
let a normal user permanently destroy real RECEIVED income history.
``is_settled`` is the single source of truth for "this transaction is
real money already exchanged" (Paid and Received both carry
``is_settled=True`` in ``ref_seeds.py``), so a boolean predicate
covers every current and future settled status without enumeration.

That last clause has since been paid out twice.  ``Settled`` -- the terminal
ARCHIVE -- joined the band and then LEFT it at plan step **balance:X-am**, and
neither change touched a line in this module, because none of these predicates
ever named a status.

**A row holding a PAYMENT or a PURCHASE is history too, whatever its status**
(plan step ``credit_card:CC-5-4a-4``, rulings **R-CC54**, **R-CC63**,
**R-CC65**).  A movement is money that moved (ruling **R-BAL80**), and a
Projected envelope holding a bank-confirmed purchase is exactly as much a
record as a Paid bill.  The ``*_holding_movements`` family below asks it of a
definition and a recurring transfer, and :func:`holds_nothing` is
the clause an archive keeps such rows OUT of; the pay-period doors ask it per
period (``pay_period_locks``).  Until that step these doors judged "history"
by row STATUS alone, and a permanent delete permitted on that answer
destroyed the purchase with its row (finding **CC-363**, measured 2026-09-22
on a copy of production: one recorded purchase under one definition).  The keys
refuse such a delete now (``fk_transaction_entries_transaction_id``, NO
ACTION), so these predicates are what turn the refusal into a designed one.
**A TRANSFER is asked the one ``transfer_legs`` question** (plan step
``balance:X-bi-6-4a-3``, ruling **R-BAL125**), never through its shadow
rows, so plan step ``balance:X-bi-6-4d-2`` moved the answer in one place: a
side's record hangs off its transfer since that step, which moved the one
join in ``transfer_legs``.
"""

from dataclasses import dataclass

from sqlalchemy.orm import InstrumentedAttribute, Query
from sqlalchemy.sql.expression import ColumnElement

from app.extensions import db
from app.models.journal_entry import Posting
from app.models.ledger_account import LedgerAccount
from app.models.merchant_rule import MerchantRule
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transfer_legs


def holds_a_movement():
    """Return the clause selecting a row that holds a payment or purchase.

    **The ONE spelling of the FILTER "does this row hold a movement"**
    (CC-5-4a-4's first review, L4): the archives' :func:`holds_nothing`
    negates it, and the pay-period doors
    (``pay_period_locks.items_holding_a_movement``) filter their row items
    on it.  Two readers ask more than it and join the movements themselves:
    :func:`rows_holding_movements` aggregates WHICH kind they are, and
    ``recurrence_engine._maintain``'s retire asks a wider question (an entry
    OR a note).  The
    relationship's own ``EXISTS`` over ``budget.transaction_entries``, hidden
    rows included -- a row's ``is_deleted`` does not change what it holds.  A
    function rather than a module constant: building a relationship's clause
    configures the mappers, which an import must not.

    **A ROW's question, not a transfer's** (plan step
    ``balance:X-bi-6-4a-3``, ruling **R-BAL125**): a transfer is asked
    ``transfer_legs.transfer_holds_a_movement``, because a side's record
    hangs off its transfer, never a row, since plan step
    ``balance:X-bi-6-4d-2``.  Until plan step ``balance:X-bi-6-4d-2`` deleted
    the shadows (ruling **R-BAL166**), a door whose row scope could reach one
    excluded them.

    Returns:
        An ``EXISTS`` clause correlated to ``Transaction``.
    """
    return Transaction.entries.any()


def holds_nothing():
    """Return the clause that keeps every row holding a movement OUT of a scope.

    The archives' (ruling **R-CC63**: "Archive hides only rows that hold
    nothing"), added to the ``Transaction`` query a bulk soft-delete runs
    over: :func:`holds_a_movement`, negated.

    Returns:
        A ``NOT EXISTS`` clause correlated to ``Transaction``.
    """
    return ~holds_a_movement()


def transfer_holds_nothing():
    """Return :func:`holds_nothing` asked of a TRANSFER: neither leg holds one.

    The transfer archive keeps a transfer either of whose legs holds a
    payment (ruling **R-CC65**): the one ``transfer_legs`` question
    (:func:`~app.services.transfer_legs.transfer_holds_a_movement`, ruling
    **R-BAL125**), negated.

    Returns:
        A ``NOT EXISTS`` clause correlated to ``Transfer``.
    """
    return ~transfer_legs.transfer_holds_a_movement()


@dataclass(frozen=True)
class HeldMovements:
    """What a set of rows holds that makes it history: payments, purchases, or both.

    The answer of the ``*_holding_movements`` family, carrying the one thing
    each door's sentence needs beyond yes / no -- WHICH kind of movement, so
    the flash names what the owner recorded (ruling **R-CC66**, "Name what it
    holds") rather than calling an unpaid envelope's purchase payment history.
    A PAYMENT is a settle's covering movement (``covers_settlement``), kept
    un-dated across a revert (ruling **R-BAL61**); a PURCHASE is everything
    else a person or the bank recorded.

    Attributes:
        payment: Any held movement is a payment.
        purchase: Any held movement is a purchase.
        live_rows: How many of the holding rows are NOT soft-deleted -- the
            rows a fallback archive leaves on the owner's budget -- counted
            as the owner sees them: a TRANSFER once for its two legs, live
            by its own ``is_deleted``, so a transfer whose two legs each
            hold its payment is one.
    """

    payment: bool
    purchase: bool
    live_rows: int

    def __bool__(self) -> bool:
        """Whether anything is held at all."""
        return self.payment or self.purchase

    @property
    def noun(self) -> str:
        """The sentence's object: 'a recorded purchase', payment, or both."""
        if self.payment and self.purchase:
            return "a recorded payment and purchase"
        return "a recorded payment" if self.payment else "a recorded purchase"

    def stays(self, thing: str, *, named: bool) -> str:
        """The clause saying which kept rows stay: '' when none is live.

        Ruling **R-CC66**'s own shape -- "the row holding it stays on your
        budget" -- counted, and with the movement NAMED where the sentence
        has not already named it (an archive's receipt).

        Args:
            thing: What a holding row is to the owner ("row", "transfer").
            named: Say what it holds (``True``), or refer back with "it"
                (``False``).  Several rows always name it, in the plural:
                the sentence before them said "a recorded purchase", which
                "them" cannot refer back to.

        Returns:
            The clause, without a capital or a full stop.
        """
        if self.live_rows == 0:
            return ""
        if self.live_rows == 1:
            what = self.noun if named else "it"
            return f"the {thing} holding {what} stays on your budget"
        if self.payment and self.purchase:
            what = "recorded payments and purchases"
        else:
            what = "recorded payments" if self.payment else "recorded purchases"
        return (
            f"the {self.live_rows} {thing}s holding {what} stay on your budget"
        )


def rows_holding_movements(*row_scope: ColumnElement) -> HeldMovements:
    """Return what the rows matching *row_scope* hold, in ONE statement.

    The row body of the ``*_holding_movements`` family, and the archives'
    own receipt (what :func:`holds_nothing` kept): :func:`_held` over every
    movement under a matching row.

    Args:
        *row_scope: Clauses over ``Transaction`` selecting the rows the door
            would remove -- soft-deleted ones included, because a delete
            takes those too.

    Returns:
        The rows' :class:`HeldMovements`.
    """
    return _held(
        db.session.query(TransactionEntry)
        .join(TransactionEntry.transaction)
        .filter(*row_scope),
        unit=Transaction.id,
        live=Transaction.is_deleted.is_(False),
    )


def _held(
    entries: Query, *, unit: InstrumentedAttribute, live: ColumnElement,
) -> HeldMovements:
    """Return the :class:`HeldMovements` of *entries*, in ONE statement.

    One aggregate, so the three facts come back together.  ``bool_or`` over
    no movements is NULL, read as ``False``.

    Args:
        entries: An unexecuted query of ``TransactionEntry`` joined to the
            unit that holds them.
        unit: The column whose distinct values ``live_rows`` counts -- a
            row's id, or a transfer's.
        live: The clause saying a unit is not soft-deleted.

    Returns:
        The entries' :class:`HeldMovements`.
    """
    payment, purchase, live_rows = entries.with_entities(
        db.func.bool_or(TransactionEntry.covers_settlement),
        db.func.bool_or(TransactionEntry.covers_settlement.is_(False)),
        db.func.count(db.distinct(unit)).filter(live),
    ).one()
    return HeldMovements(
        payment=bool(payment), purchase=bool(purchase), live_rows=live_rows,
    )


def template_holding_movements(template_id: int) -> HeldMovements:
    """Return what a definition's rows hold -- any row, any status, soft-deleted too.

    Asked by ``routes/templates/crud.hard_delete_template`` after
    :func:`template_has_paid_history` and before the merchant-rule question: a
    definition whose rows hold a payment or purchase is archived instead of
    deleted (ruling **R-CC54**), because its permanent delete removes every
    non-settled row it names, soft-deleted ones included.

    Args:
        template_id: The TransactionTemplate.id to check.

    Returns:
        Its rows' :class:`HeldMovements`; falsy when none holds one.
    """
    return rows_holding_movements(Transaction.template_id == template_id)


def transfer_template_holding_movements(template_id: int) -> HeldMovements:
    """Return what a recurring transfer's legs hold -- any transfer, soft-deleted too.

    A Projected transfer holds a movement only as a reverted settle's KEPT
    payment, which may still be matched to the bank's line.  Its permanent
    delete removes every non-settled transfer through the transfer service,
    so one holding a payment is archived instead (ruling **R-CC65**).

    Args:
        template_id: The TransferTemplate.id to check.

    Returns:
        Its legs' :class:`HeldMovements`; falsy when none holds one.
    """
    return transfers_holding_movements(
        Transfer.transfer_template_id == template_id,
    )


def transfers_holding_movements(*transfer_scope: ColumnElement) -> HeldMovements:
    """Return what the legs of the transfers matching *transfer_scope* hold.

    The recurring-transfer permanent delete's refusal
    (:func:`transfer_template_holding_movements`) and the transfer archive's
    receipt (``routes/transfers/lifecycle._archive``) ask it.  Over
    the one ``transfer_legs`` join the pay-period doors' question reads
    (:func:`~app.services.transfer_legs.held_transfer_entries`, ruling
    **R-BAL125**), and counted by TRANSFER, so a transfer whose two legs
    each hold its payment is one kept transfer to the owner.

    Args:
        *transfer_scope: ``Transfer`` filter clauses.

    Returns:
        The legs' :class:`HeldMovements`; falsy when none holds one.
    """
    return _held(
        transfer_legs.held_transfer_entries(*transfer_scope),
        unit=Transfer.id,
        live=Transfer.is_deleted.is_(False),
    )


def template_has_paid_history(template_id: int) -> bool:
    """Check if a transaction template has any settled transactions.

    "Settled" is determined by the semantic ``Status.is_settled``
    boolean (Paid and Received in the current seed -- see
    ``ref_seeds.py``).  Enumerating status names or IDs here would
    silently miss any status added to the settled set in the
    future; the boolean column is the single source of truth.
    Audit reference: CRIT-05 / E-22 (the prior ``[DONE, SETTLED]``
    enumeration omitted RECEIVED and enabled irreversible RECEIVED
    income-history deletion).

    **A SOFT-DELETED settled row is payment history too, and dropping the
    ``is_deleted`` filter is what closes finding N-444** (ruling **R-JE**,
    developer 2026-09-03).  This predicate gates
    ``routes/templates/crud.hard_delete_template``, whose bulk delete filters
    on ``Status.is_settled`` and NOT on ``is_deleted`` -- so a soft-deleted
    settled row was invisible to the gate AND excluded from the delete, and the
    template went while the row stayed.  The template key was ON DELETE SET
    NULL then, so what was left was a settled row carrying its money and no
    link to the definition that made it.  Harmless while such a row OWNED its
    figure; plan step **balance:X-au-e** declares every non-override template
    row DERIVED, at which point the same survivor carries
    ``amount_source_id = template`` with no template to read -- unpriceable by
    ``_stated_amount`` on any revert to Projected, and unrestorable by
    ``d7b2e6c1a483``'s downgrade, whose restore joins on the ``template_id``
    that is now NULL (ledger row **N-440**).  The two halves of the door now
    ask the same question, so the state is refused rather than guarded -- and
    since plan step **balance:X-bi-7d-2** the key is
    ``fk_transactions_template_id`` ON DELETE RESTRICT, so the database
    refuses it too: a definition whose row survived the filtered delete cannot
    be deleted at all, and this gate is what turns that refusal into a
    designed one.

    The refusal is not free and the developer took it deliberately: a template
    whose only settled rows were soft-deleted can no longer be permanently
    deleted, and is archived instead.  Zero such rows exist on the 2026-09-03
    production clone, so nothing that is deletable today stops being so.

    Args:
        template_id: The TransactionTemplate.id to check.

    Returns:
        True if at least one linked transaction has a settled status,
        whether or not it is soft-deleted.
    """

    return db.session.query(
        db.session.query(Transaction)
        .join(Status, Transaction.status_id == Status.id)
        .filter(
            Transaction.template_id == template_id,
            Status.is_settled.is_(True),
        ).exists()
    ).scalar()


def template_has_standing_rule(template_id: int) -> bool:
    """Check if a standing merchant rule files a merchant's spending here.

    **The template twin of :func:`category_has_usage`'s merchant-rule clause**,
    and it exists for the same reason and closes the same defect on the other
    of the two cascading subject keys (plan step ``bank_import:X-gd-2``).
    ``fk_merchant_rules_template_account`` is ``ON DELETE CASCADE``, so
    permanently deleting a template destroys every rule that names it -- and
    ``hard_delete_template`` gated only on settled TRANSACTIONS, which knows
    nothing about rules.

    Measured on the developer's own dev database, 2026-08-26: 16 of 29 rules
    name a template, and template 19 (`Clothes`) carries a rule and ZERO
    settled transactions -- so the permanent-delete arm was live on it and one
    press would have destroyed a stated answer under a flash that said only
    that the template was deleted.  Ruling **R-GS** is what makes it matter
    rather than merely untidy: a rule row is never un-stated by the owner, so
    a silent cascade would be the only way one could vanish.

    **It is NOT folded into :func:`template_has_paid_history`**, which is cited
    by name from the transfer-template route's own guard and means exactly what
    it says.  Two predicates with two reasons give the door two SENTENCES, and
    telling an owner their template "has payment history" when what it has is a
    merchant rule is the screens-stating-what-is-false defect this arc has
    closed three times.

    Args:
        template_id: The TransactionTemplate.id to check.

    Returns:
        True if any :class:`~app.models.merchant_rule.MerchantRule` names it.
        **Not scoped**, for the reason ``category_has_usage``'s own clause is
        not: ``fk_merchant_rules_template_account`` is composite over
        ``(template_id, account_id)``, and ``transaction_templates.id`` is a
        primary key -- so a rule naming this template can only be on this
        template's account, and its owner is that account's.
    """

    return db.session.query(
        db.session.query(MerchantRule).filter(
            MerchantRule.template_id == template_id,
        ).exists()
    ).scalar()


def transfer_template_has_paid_history(template_id: int) -> bool:
    """Check if a transfer template has any settled transfers.

    Filters on the semantic ``Status.is_settled`` boolean so Received and any
    future settled status are covered without enumeration.  Audit reference:
    CRIT-05 / E-22.

    **It MIRRORS :func:`template_has_paid_history` again, and plan step
    balance:X-au-f is the edit this predicate was owed** (ruling **R-JE**).
    That twin dropped its ``is_deleted`` filter at X-au-e because a
    soft-deleted settled TRANSACTION survives its template's hard delete
    carrying a declaration nothing can price.  This one kept the filter while
    the same survivor was unreachable here -- a generated TRANSFER still stored
    its own amount, so a survivor whose ``transfer_template_id`` the delete set
    to NULL was priced by rule 1 off the column it held.  X-au-f empties that
    column, so the survivor is reachable now -- and the failure comes EARLIER
    than its transaction twin's, which is worth stating because a first draft of
    this paragraph transplanted the twin's account.  On the ROW table the
    survivor is left behind holding a declaration nothing can price; on THIS
    table ``ck_transfers_adhoc_owns_amount`` (*a declaration requires a
    template*) meets ``fk_transfers_transfer_template_id``'s ON DELETE SET NULL,
    so the delete itself raises ``IntegrityError`` -- an unhandled 500 on the
    delete route rather than a quietly unpriceable row.  Either way, filtering
    the survivor out here is what lets that delete be attempted at all.

    **A soft-deleted row is history like any other for THIS question**, which
    is why the fix is dropping the filter rather than adding a repair: the
    predicate asks whether anything settled against this definition, and
    deleting the row that settled does not un-settle it.

    Args:
        template_id: The TransferTemplate.id to check.

    Returns:
        True if at least one linked transfer has a settled status, soft-deleted
        ones included.
    """

    return db.session.query(
        db.session.query(Transfer)
        .join(Status, Transfer.status_id == Status.id)
        .filter(
            Transfer.transfer_template_id == template_id,
            Status.is_settled.is_(True),
        ).exists()
    ).scalar()


def account_has_history(account_id: int) -> bool:
    """Check if an account has any non-deleted transactions.

    Unlike the template history checks, this does NOT filter by
    status.  Any non-deleted transaction means the account has
    history.  This is intentionally stricter than the template
    functions because account deletion would cascade to all related
    financial records -- even Projected transactions represent
    user-entered data worth preserving.

    **A live row of a DEFINITION on this account counts too** (plan step
    ``balance:X-bi-7a``, developer ruling 2026-09-13).  The hard-delete door
    refuses an account only for its RECURRING definitions now (ruling
    **R-BAL23**), and a rule-less one goes with the account when it defines
    nothing -- so the row that makes it define something has to be seen here
    even when it sits on another account, which a settled, an overridden or
    a soft-deleted row does after its definition's account moved (the
    maintain pass rewrites only a Projected, un-edited row; through
    ``credit_card:CC-5-1`` it also RETAINED a row holding the owner's records
    where it was, an arm ruling **R-CC36** retired for transaction
    definitions).  ``transactions.template_id`` was
    ``ON DELETE SET NULL`` until the family's cutover (``X-bi-7d-2``):
    deleting such a definition under a live derived row left one with no
    definition to price it, which ``cash_ledger`` refuses on every screen
    that loads it; the key is ``RESTRICT`` now, so the database refuses the
    delete and this count is what makes the refusal a designed one.

    **A live TRANSFER from or to this account counts in its own right**
    (plan step ``balance:X-bi-6-4c-4``, finding **BAL-539**).  Until then it
    counted only through its shadow row on the account, and plan step
    ``X-bi-6-4d`` deleted every shadow (ruling **R-BAL166**); without this
    arm the door would permanently delete, where it archives, an account a
    live transfer names that guard 2 does not see.  Guard 2 asks
    only whether a transfer definition names the account NOW (one-time and
    archived ones included), so on an account none names it lets through
    every transfer from or to the account: one that names no definition,
    such as an ad-hoc transfer (until plan step ``balance:X-ci-3`` makes
    every transfer name one), or one whose definition names other accounts,
    such as one its definition's endpoint edit did not move.  This arm
    counts every live transfer from or to the account, so it needs no list
    of how a transfer comes to be one of those.  It was byte-identical to the
    shadow arm while the shadows existed: a live transfer's two live shadows
    sat on its two endpoints (the create door wrote them there and an
    endpoint move re-pointed both), and a soft delete flagged all three rows,
    so the two arms answered alike for every account -- 0 accounts differed
    on the 2026-09-30 00:11 production dump.

    Args:
        account_id: The Account.id to check.

    Returns:
        True if the account has any non-deleted transaction history, on it or
        under one of its definitions, or any live transfer from or to it.
    """

    return db.session.query(
        db.or_(
            db.session.query(Transaction)
            .outerjoin(Transaction.template)
            .filter(
                db.or_(
                    Transaction.account_id == account_id,
                    TransactionTemplate.account_id == account_id,
                ),
                Transaction.is_deleted.is_(False),
            ).exists(),
            db.session.query(Transfer).filter(
                db.or_(
                    Transfer.from_account_id == account_id,
                    Transfer.to_account_id == account_id,
                ),
                Transfer.is_deleted.is_(False),
            ).exists(),
        )
    ).scalar()


def account_holds_other_rows_movements(account_id: int) -> bool:
    """Check if money moved through an account from rows that are not its own.

    The MOVEMENT arm of :func:`account_has_history` (plan step
    ``credit_card:CC-5-2``).  A purchase names the account its money moved
    through (ruling **R-CC15**; a movement folds there, **R-BAL75**), and
    since ``CC-5-1`` dropped the key that held it to its row's account, a
    card can hold swipes recorded in checking envelopes: real history on the
    card, under rows the row arm never counts because they are checking's.
    ``fk_transaction_entries_account_id`` is ``ON DELETE RESTRICT`` (ruling
    **R-CC32**), so an account holding such a movement met the hard-delete
    door's ``DELETE`` as a 500 rather than as the designed archive every
    other kind of history gets.

    **The set is the movements ON this account whose row the door's cleanup
    does not remove**, stated the way the row arm states its own.  The door
    deletes every row ON the account (step 2) and every row UNDER one of the
    account's rule-less definitions (step 2b,
    ``definition_delete.permanently_delete_definition``; guard 3 has already
    refused a recurring one), so a movement on this account under a row that
    is NEITHER -- a plan item that lives elsewhere and whose money crossed
    here -- is what this counts, whatever its row's ``is_deleted`` says.  (A
    hidden row holds none, which the database holds: none can arrive under a
    hidden row, ruling **R-CC89**; no row can be hidden holding one,
    **R-CC92**; neither in either order of a race, because the arrival check
    locks the row, **R-CC96**; and the release's migration refuses to inherit
    one, **R-CC82**.  The count relies on none of this.)  The row is not
    checking's ghost to hard-delete, and the swipe still names the card.  A
    movement under a row the cleanup DOES remove was asked by the door's own
    arm from plan step ``credit_card:CC-5-4a-4`` (until then a ghost's
    movement cascaded with it, ``fk_transaction_entries_owner_transaction``)
    until plan step ``balance:X-bi-6-4d-2`` deleted that arm: a live row
    holding one is history, and the database refuses a hidden one.

    Args:
        account_id: The Account.id to check.

    Returns:
        True if any ``budget.transaction_entries`` row names this account
        while its parent row sits on another account and under no definition
        of this account's.
    """
    return db.session.query(
        db.session.query(TransactionEntry)
        .join(TransactionEntry.transaction)
        .outerjoin(Transaction.template)
        .filter(
            TransactionEntry.account_id == account_id,
            Transaction.account_id != account_id,
            db.or_(
                Transaction.template_id.is_(None),
                TransactionTemplate.account_id != account_id,
            ),
        ).exists()
    ).scalar()


def account_has_ledger_postings(account_id: int) -> bool:
    """Check if any of an account's ledger accounts has postings.

    A settled transfer writes balanced journal entries onto the account's
    linked ledger account (Build-Order Step 2).  Those entries are immutable
    and SURVIVE a transfer delete, which removes the transfer's movements and
    so SET-NULLs their entries' ``journal_entries.transaction_entry_id``, so
    an account can still hold posting legs after every transaction
    referencing it is gone (e.g. its ad-hoc transfer was hard-deleted).
    Hard-deleting such an account would CASCADE-delete only its own legs and
    strand the paired legs as unbalanced single-leg entries (the balanced
    trigger fires on INSERT/UPDATE, not DELETE), so the hard-delete guard
    archives it instead.  The posting-ledger counterpart of
    :func:`account_has_history`.

    **It joins by ``account_id`` across ALL kinds, and that is load-bearing
    rather than incidental.**  An account carries its ``linked`` row plus its
    per-account COUNTER rows, and since ruling **R-FO** a correction re-pointed
    from one counter row to another emits an entry whose ONLY legs are counter
    legs -- so a linked-row-scoped check would answer False for an account that
    really does hold immutable posted history, and the hard delete would
    proceed.  The kind-agnostic join is what makes the model's
    cascade-imbalance impossibility argument true by construction rather than
    by a premise about which rows corrections touch.

    Args:
        account_id: The Account.id to check.

    Returns:
        True if any ledger account linked to *account_id* has at least one
        posting.
    """

    return db.session.query(
        db.session.query(Posting)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(LedgerAccount.account_id == account_id)
        .exists()
    ).scalar()


def category_has_usage(category_id: int, user_id: int) -> bool:
    """Check if a category is in use by templates, transfers, transactions or rules.

    Performs a four-part check, short-circuiting in the order it runs them:
    (1) any TransactionTemplate with matching category_id and user_id, (2) any
    standing merchant rule that names it -- as the category a *new envelope*
    answer creates under, OR as the income category a deposit from that
    merchant is filed under (plan step ``bank_import:X-gj-2a``) -- (3) any
    TransferTemplate or Transfer with matching category_id and user_id, in
    one statement, and (4) any Transaction with matching category_id joined
    to PayPeriod filtered by user_id.  The join is last because it is the
    only one of the four that needs one; none of the five tables has an index
    on ``category_id``, so every part is a sequential scan over a small table
    and the ordering buys the JOIN rather than a lookup.

    The user_id scoping is critical for (1), (3) and (4) -- categories are
    user-scoped, and the check must not cross user boundaries.

    **The transfer part was added at plan step ``balance:X-bi-6-4c-4``**
    (finding **BAL-545**).  Until then a transfer counted only through its
    shadow rows in (4), and a recurring transfer DEFINITION not at all: a
    category that only a transfer definition named read as unused, and a
    permanent delete set ``transfer_templates.category_id`` to NULL
    (``ON DELETE SET NULL``) under a flash that said nothing about it.  That
    was the one change it made then: the ``Transfer`` arm added nothing,
    because each transfer's expense shadow carried the transfer's own
    category (0 of 179 differed on the 2026-09-30 00:11 production dump).
    Plan step ``X-bi-6-4d`` then deleted every shadow (ruling **R-BAL166**),
    so a transfer's category is protected here or nowhere.  Soft-deleted
    transfers count, as soft-deleted transactions always have in (4).  The
    shadows took one of (4)'s answers with them: 74 income shadows written
    before the create door mirrored the parent's category carried the user's
    "Transfers: Incoming" category instead, which (4) counted as usage, so a
    category only those shadows used is deletable now (finding **BAL-575**).

    **The merchant-rule part was added at plan step ``bank_import:X-gd-2``, and
    it is about what ``delete_category`` does with the answer.**  A "no" here is what
    permits a PERMANENT delete, and ``fk_merchant_rules_category_owner``
    cascades -- so a category no template and no transaction used, but that one
    merchant rule filed under, was destroyed together with the rule, under a
    flash reading "permanently deleted" that said nothing about the rule.  The
    cascade itself is right (an answer naming a category that no longer exists
    is not an answer); what was wrong was a door calling the category unused
    while a stored decision used it.  Ruling **R-GS** makes that worse rather
    than better: a rule row is never un-stated by the owner, so a silent
    cascade would be the only way one could vanish.  Measured on the
    developer's dev database, 2026-08-26: 12 new-envelope rules naming 6
    distinct categories, every one of them also used by a template or a
    transaction -- so the path is reachable and has not yet fired.

    **It is NOT scoped, and that is structural rather than an omission.**  The
    two clauses beside it filter on ``user_id`` because they must:
    ``transaction_templates.category_id`` and ``transactions.category_id`` are
    plain single-column keys, so either can name a category belonging to
    somebody else and the reader is what stops it.  A rule cannot:
    ``fk_merchant_rules_category_owner`` is composite over
    ``(category_id, user_id)`` against ``categories(id, user_id)``, and
    ``categories.id`` is a primary key -- so a ``category_id`` DETERMINES its
    owner and a rule naming one necessarily carries that owner's id.  A
    ``user_id`` term here would restate what the constraint already holds, and
    no test could make it fire; an adversarial review 2026-08-26 found the case
    written for it grading a different scenario for exactly that reason.

    **What it deliberately does NOT filter is the ACCOUNT.**  A rule is
    account-scoped and a category is not, so one owner's rules on two accounts
    may both file under one category and BOTH are usage.

    Args:
        category_id: The Category.id to check.
        user_id: The user who owns the category (for ownership scoping).

    Returns:
        True if any templates, transfers, transfer definitions, transactions
        or standing merchant rules reference this category for the given
        user.
    """

    # Check templates first -- cheap query with direct user_id column.
    has_templates = db.session.query(
        db.session.query(TransactionTemplate).filter_by(
            category_id=category_id, user_id=user_id,
        ).exists()
    ).scalar()

    if has_templates:
        return True

    # ...then the merchant rules, before the join below is paid for.  No
    # ``user_id`` term: ``fk_merchant_rules_category_owner`` is composite over
    # ``(category_id, user_id)`` and ``categories.id`` is a primary key, so a
    # rule naming this category can only be this owner's.  No ``account_id``
    # term either, and that one IS load-bearing: a category is owner-scoped
    # while a rule is account-scoped, so two accounts' rules may file under one
    # category and both are usage.
    #
    # **BOTH answer columns, since plan step ``bank_import:X-gj-2a``.**  A rule
    # names a category in two different ways now -- ``category_id`` is the
    # category a *new envelope* answer creates its envelope under, and
    # ``income_category_id`` is what a DEPOSIT from that merchant is -- and
    # ``fk_merchant_rules_income_category_owner`` cascades exactly as its twin
    # does.  Asking about only the first would have re-created, on the new
    # column, the precise defect this clause was added for: a category no
    # template and no transaction used but that one income rule filed under
    # would be reported UNUSED, permanently deleted, and take the rule with it
    # under a flash saying nothing about it.  A rule is never un-stated by the
    # owner (ruling **R-GS**), so that cascade is the only way one can vanish.
    has_rules = db.session.query(
        db.session.query(MerchantRule).filter(
            db.or_(
                MerchantRule.category_id == category_id,
                MerchantRule.income_category_id == category_id,
            ),
        ).exists()
    ).scalar()

    if has_rules:
        return True

    # ...then the transfers and their definitions, both scoped by their own
    # ``user_id``: ``transfers.category_id`` and
    # ``transfer_templates.category_id`` are single-column keys, so either can
    # name another owner's category and the reader is what stops it.
    has_transfers = db.session.query(
        db.or_(
            db.session.query(TransferTemplate).filter_by(
                category_id=category_id, user_id=user_id,
            ).exists(),
            db.session.query(Transfer).filter_by(
                category_id=category_id, user_id=user_id,
            ).exists(),
        )
    ).scalar()

    if has_transfers:
        return True

    # Check transactions -- requires join through PayPeriod for user scoping.
    return db.session.query(
        db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.category_id == category_id,
        ).exists()
    ).scalar()
