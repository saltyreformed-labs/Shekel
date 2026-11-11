"""Shekel Budget App -- Account Opening Equity Model (budget schema).

**What an account held before its records begin, as a RECORDED FACT** (plan
step X-f3c-2a, ruling **R-GX**).  One append-only row per restatement; the
latest recorded row governs.

**The quantity, in one sentence.**  An account's opening equity is the capital
its books opened with -- the level every balance the app has ever rendered is
stacked on top of.  On the developer's own Checking account it is ``$689.16``:
the ``$2,746.58`` asserted for 2026-03-27 less the ``$2,057.42`` of movements
that assertion already contained.

**It was INFERRED on every read until this step, and each of the four defects
that caused has the same root.**  The balance fold re-derived it as
"the earliest assertion minus the movements dated at or before it"
(``balance_at._cash_fold._actual_steps``' seed, ruling **R-I**), which meant:

1. **It moved when an assertion was BACK-DATED.**  Which assertion was "the
   opening" was decided by SORT POSITION -- ``is_opening = index == 0`` over
   ``(observed_on, created_at, id)`` in ``cash_ledger._events`` -- and
   ``anchor_service.resolve_observation_day`` permits a back-dated assertion.
   Recording a balance for a day earlier than any on file silently re-elected
   the opening, recomputed the constant, and moved every pre-opening balance,
   with no surface saying so.
2. **It differed per SCENARIO.**  The subtraction ran over
   ``settled_cash_facts(account_id, scenario_id)``, so two scenarios holding
   different settled rows produced two opening equities for one real bank
   account.  Money that existed before tracking began cannot be a function of
   a what-if.
3. **It could not be CORRECTED.**  Finding **N-275**: account 1's opening
   asserts ``$2,746.58`` for 2026-03-27 where the bank's own closing that day
   is ``$3,182.63``, so the inferred figure is wrong by ``$436.05`` and there
   was nowhere to say so.
4. **It was derived TWICE.**  The read fold seeded it; the posted ledger
   booked it again as the ``account_opening`` journal entry's linked leg
   (``asserted - ledger_before``).  Measured on a production clone 2026-08-27
   the two agree to the cent on all seven non-loan accounts -- because both ran
   the same subtraction, not because either read a fact.

This table is the fact both now read.

**Why a satellite table rather than a column or a journal entry** (developer,
2026-08-27).  A column on ``budget.accounts`` would have to be NULLABLE for an
amortizing loan, a rule the schema cannot state because the kind lives on
``ref.account_types``, and it would sit exactly where ruling **R-EH** deleted
``accounts.current_anchor_balance``.  The posted ``account_opening`` journal
entry cannot be the source of truth for three separate reasons: it is
scenario-scoped (defect 2 returns), a ``$0.00`` opening books no entry at all
so "zero" is indistinguishable from "none", and the posted ledger is a
PROJECTION of the walk in this codebase, so reading it from the balance seam
would invert the arc's own dependency direction.  What is left is a per-account,
scenario-free, always-present record -- the shape ``loan_params`` /
``interest_params`` / ``investment_params`` already use.

**EVERY account carries one, including an amortizing loan, and that is a
REACHABILITY fact rather than a tidiness preference.**  The spec's first draft
said the two loans get no row.  They must: ``balance_at.balance_at`` -- the
KIND-CORRECT public entry the savings page and the net-worth surfaces read --
dispatches on ``_resolution.configured_loan``, which returns ``None`` for an
amortizing account carrying no :class:`~app.models.loan_params.LoanParams`, and
falls through to the replay over this fold.  That state is ordinary (create a
Mortgage, do not finish the loan-params form), so a loan without a row would
raise on a live screen.  With every account carrying one, absence is
unreachable and :func:`app.services.cash_ledger.account_opening_fact` refuses it
rather than falling back to a fabricated ``$0.00``.  A CONFIGURED loan never
reads its row: its opening is ``LoanParams.original_principal``, materialised
by ``loan_loaders.synthesize_origination_anchor``.

**It carries the DAY as well as the amount, and fixing only the amount would
have left half of defect 1 alive.**  ``opened_on`` is the civil day the books
opened.  The worked-out ledger's ``account_opening`` correction is dated on
it, as the stored ledger's journal entry was until plan task plan#3133; before
this step that entry was dated on whichever assertion sorted first, so
back-dating still silently re-dated the opening posting and demoted the
previous one to a true-up.  With the day stored, ``is_opening`` stops existing
as a money concept: this row books the ``account_opening`` kind and every
assertion books ``account_trueup``, with no positional flag left to read.

**THE EQUITY IS THE BALANCE AT THE CLOSE OF ``opened_on``, so no cash movement
may be dated ON OR BEFORE that day** (plan step X-f3c-2b, ruling **R-HG**).
That is the same rule ``account_anchor_history.observed_on`` states for an
assertion (ruling R-DH (a)) -- "a source dated at or before this day is already
inside this figure" -- and adopting it is what makes the table's two writers
mean one thing.  ``account_service.create_account`` stores the balance a human
typed "as of" a day, which is that day's close; migration ``a7c41f9d2b60``
derived a level from BEFORE the earliest recorded movement while dating it at
the earliest ASSERTION.  One column, two semantics, differing by whatever moved
that day -- and where the movement is not absorbed, counted twice (finding
**N-378**: the fold seeds here and ``dated_deltas`` emits the row at its own
day, so the running total carries it again until an assertion resets it, and on
a MODELLED account that reset books to ``unrealized_change``, turning a transfer
into market performance that never unwinds).

**The rule is enforced where it cannot be skipped, not where it is convenient.**
:func:`app.services.cash_ledger.reject_movement_before_books_open` refuses it at
:func:`app.services.settle_day.record_settle_day` -- the ONE assignment of
``settled_on`` on either movement table -- and at
``reconcile_service.record_settled_days``, the bulk ``UPDATE`` that has no ORM
instance to hand that function.  What makes the state UNSTORABLE rather than
merely refused is :mod:`app.opening_infrastructure`: deferrable constraint
triggers over ``budget.transactions``, ``budget.transaction_entries`` and this
table, so the rule holds in BOTH directions -- an opening cannot be restated
FORWARD past a movement either -- and against a writer nobody enumerated.

**Append-only, latest governs -- the same shape as its two siblings.**
:class:`~app.models.account.AccountAnchorHistory` records what a bank said and
:class:`~app.models.loan_anchor_event.LoanAnchorEvent` records what a loan owed;
both are append-only and this is the third member of that family.  An UPDATE
would make a restatement invisible to the app -- ``system.audit_log`` would hold
it, but nothing could render it, which is the exact gap finding **N-205** built
the balance-history card to close.  Restating the number every balance rests on
is not a state this app may enter silently.

**"Latest governs" is NOT the positional read this step deletes**, and the
difference is which clock orders it.  ``is_opening`` inferred a TYPE from
position in a list ordered by ``observed_on`` -- a business date the owner may
back-date -- so an ordinary user action changed the inference.  This orders by
``id``, the sequence value the INSERT allocates, which no door lets a user
move.

**It ordered by ``created_at`` first until plan step X-f3c-2b-2a, on a reason
that was false.**  ``created_at`` comes from :class:`CreatedAtMixin`'s
``db.func.now()``, which PostgreSQL evaluates at TRANSACTION START -- so two
restatements made in two tabs can carry instants in the opposite order to their
commits, and the later statement then sorts BELOW the one it supersedes.  See
:data:`app.opening_infrastructure.GOVERNING_ORDER_SQL` for the worked failure
and why ``id`` alone is monotone under the owner's write lock.

Reads: :func:`app.services.cash_ledger.account_opening_fact`, and its
non-raising twin :func:`app.services.cash_ledger.governing_account_opening` for
the write door.  **Writes: ONE function in ``app/``**,
:func:`app.services.opening_service.stage_account_opening` -- reached by
``account_service.create_account`` for the origination and by
``opening_service.apply_opening_restatement`` for an owner correcting the
figure, both ``user_declared``.  That single-writer shape is ruling **R-ES**
applied to this table (plan step X-f3c-2b-2a): the did-this-change compare and
the audit line are properties of the TABLE rather than of whichever event did
the INSERT.  *The owner's write lock was one of them until plan step
``balance:X-bn`` made it the signed-in request's transaction's; an origination
at registration takes none, its account being invisible to every other
transaction.*

**TWO migrations also write here, and the first statement of this census named
only one** (adversarial review, 2026-08-31).  ``a7c41f9d2b60`` seeds every
pre-existing account at the derived figure (``migration_derived``), and
``d3b6f1c8a274``'s ``_restate`` appends a row per account whose books had to
move back to reach its earliest recorded movement.  Both are frozen history
rather than live writers -- ruling **R-HY** already forbids a migration
rewriting one of these rows -- but a census that claims completeness and misses
a writer is the shape this project has paid for before, so both are named.
``tests/_test_helpers.restate_account_opening`` is a THIRD, deliberately
outside the door: it exists so a fixture can open books before the pay calendar
begins, which no service bound permits.
"""

from app.extensions import db
from app.models.append_only import (
    AppendOnlyViolation,
    install_append_only_guards,
)
from app.models.mixins import AccountScopedMixin, CreatedAtMixin


class AccountOpeningImmutableError(AppendOnlyViolation):
    """Raised when ORM code tries to UPDATE or DELETE an AccountOpening.

    The table is append-only: a restatement is a NEW row, so the record of
    what the opening used to be survives it.  The twin of
    :class:`~app.models.loan_anchor_event.LoanAnchorEventImmutableError` and of
    :class:`~app.models.account.AccountAnchorHistoryImmutableError`, all three
    installed by :func:`app.models.append_only.install_append_only_guards`.

    **This is the NAMED half of the refusal, not the whole of it** (plan step
    X-f3c-2c, ruling **R-HY**).  The listener sees only ORM-mediated writes; a
    bulk ``query.update()``, a raw statement and a psql session are refused by
    ``budget.refuse_append_only_change``, the trigger
    :mod:`app.append_only_infrastructure` installs on this table.  The
    ``ON DELETE CASCADE`` from ``budget.accounts`` is deliberately outside BOTH
    -- it is the documented disposal path for an account's whole history.
    """


class AccountOpening(AccountScopedMixin, CreatedAtMixin, db.Model):
    """One statement of what an account held before its records begin.

    See the module docstring for what the quantity is, why it is stored, and
    why every account carries one.  The GOVERNING row for an account is the one
    with the greatest ``(created_at, id)``; earlier rows are the restatement
    history.

    Attributes:
        opened_on: The civil day the account's books opened -- the day this
            equity was the whole of what the account held.  A recorded fact,
            not ``min(assertion.observed_on)``, which is what let a back-dated
            assertion re-date the opening.
        opening_equity: The capital the books opened with, LEDGER-NATIVE and
            signed exactly as an assertion's ``anchor_balance`` is (ruling
            R-J: the walk never branches on account class, and neither does
            this).  ``Decimal`` money, never ``float``.
        source_id: Where the figure came from --
            :class:`~app.enums.AccountOpeningSourceEnum` through
            ``ref.account_opening_sources``.  A ``migration_derived`` figure is
            the pre-X-f3c-2a inference frozen and may be wrong (**N-275**
            measures one wrong by ``$436.05``); a ``user_declared`` one is an
            observation.  A reader that cannot tell them apart presents a guess
            and a fact identically.
        created_at: The recording instant, from :class:`CreatedAtMixin`.  It
            ORDERS the restatements and dates nothing -- ``opened_on`` is the
            business date, exactly the two-clock split
            :class:`~app.models.account.AccountAnchorHistory` documents.

    ``account_id`` is NOT unique, which is the whole of what makes this
    append-only rather than a mutable scalar: :class:`AccountScopedMixin`
    rather than the ``AccountScopedUniqueMixin`` the 1:1 params satellites use.
    """

    __tablename__ = "account_openings"
    __table_args__ = (
        # The per-account lookup the loader reads, leading on ``account_id``
        # and carrying ``created_at`` so "the governing row" is an index scan
        # rather than a sort -- the same shape and the same reason as
        # ``idx_anchor_history_account`` on the assertion table.
        db.Index(
            "idx_account_openings_account",
            "account_id",
            "created_at",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    opened_on = db.Column(db.Date, nullable=False)
    # NO sign CHECK, and its absence is a statement rather than an omission.
    # The figure is LEDGER-NATIVE: positive for an asset's opening capital and
    # negative for a liability's (the Van Loan's derived opening is
    # ``-$531.94``), and ruling **R-J** forbids this tier branching on account
    # class -- so there is no sign rule to write down.  ``Numeric(12, 2)`` and
    # NOT NULL are the constraints the quantity actually has.  Contrast
    # ``loan_anchor_events.anchor_balance``, which carries ``>= 0`` because a
    # loan's OWED is positive-only by construction.
    opening_equity = db.Column(db.Numeric(12, 2), nullable=False)
    source_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.account_opening_sources.id",
            name="fk_account_openings_source_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    def __repr__(self):
        return (
            f"<AccountOpening account={self.account_id} "
            f"opened_on={self.opened_on} equity={self.opening_equity}>"
        )


install_append_only_guards(AccountOpening, AccountOpeningImmutableError)
