"""
Shekel Budget App -- Transfer Model (budget schema)

Tracks transfers between accounts (checking ↔ savings) within pay periods.
Supports the transfers a recurring definition generates, the one-time transfer a
definition with no rule places, and ad-hoc transfers that name no definition.
"""


from sqlalchemy.ext.hybrid import hybrid_property

from app.extensions import db
from app.models._derived_flag import DerivedFlag
from app.models.amount_ownership import from_columns
from app.models.settled_figure import settled_figure_from_columns
from app.models.mixins import (
    OptimisticLockMixin,
    SoftDeleteOverridableMixin,
    TimestampMixin,
    UserScopedMixin,
)


class Transfer(
    UserScopedMixin, OptimisticLockMixin, SoftDeleteOverridableMixin,
    TimestampMixin, db.Model,
):
    """A transfer between two accounts within a pay period.

    Optimistic locking: ``version_id`` is the SQLAlchemy
    ``version_id_col`` for the row.  Every ORM-emitted UPDATE or
    DELETE is narrowed to ``WHERE id = ? AND version_id = ?`` and
    the stored value is atomically incremented; concurrent
    mutations race for the bump and the loser raises
    :class:`sqlalchemy.orm.exc.StaleDataError`.  Until plan step
    ``balance:X-bi-6-4d-2`` deleted the transfer's two shadow
    transactions, the transfer service propagated parent-transfer
    mutations to both, so the parent's version pin protected the
    three-row write set even though the shadow rows carried their
    own ``version_id`` columns.  See commit C-18 of the 2026-04-15
    security remediation plan.
    """

    __tablename__ = "transfers"
    __table_args__ = (
        db.Index("idx_transfers_period_scenario", "pay_period_id", "scenario_id"),
        db.CheckConstraint(
            "from_account_id != to_account_id",
            name="ck_transfers_different_accounts",
        ),
        db.CheckConstraint("amount > 0", name="ck_transfers_positive_amount"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transfers_version_id_positive",
        ),
        # THE AMOUNT MODEL'S ONE CONSTRAINT, on the second of the two columns it
        # covers (ruling **R-FI**, plan step X-au-c1).  Identical in form and in
        # purpose to ``ck_transactions_amount_ownership`` -- see that constraint
        # for why the pairing is written as two NULL tests rather than against a
        # source value, and for the write-side teeth it buys.  On THIS table
        # this comment named two writers: ``transfer_service`` copied the
        # parent's figure onto both shadows, and a drift corrector repaired the
        # copies that got away.  Plan step X-au-g-2c-2 deleted both by making a shadow READ
        # its parent (``1f2b98a40``, ruling **R-IN**, which took in X-au-f's
        # shadow half), and plan step ``balance:X-bi-6-4d-2`` deleted the
        # shadows.
        #
        # ``ck_transfers_positive_amount`` (``amount > 0``) is UNCHANGED and
        # still admits the NULL (a comparison with NULL is UNKNOWN, which a CHECK
        # passes), so this constraint alone decides when the column may be empty.
        db.CheckConstraint(
            "(amount_source_id IS NULL) = (amount IS NOT NULL)",
            name="ck_transfers_amount_ownership",
        ),
        # An AD-HOC transfer owns its amount, because no definition states a
        # price for it -- ``cash_ledger.resolve_transfer_amount`` answers OWN for
        # a transfer with no template, so a declaration on one names a relation
        # that cannot be reached.
        #
        # **It is a constraint rather than a comment because
        # ``uq_transfers_adhoc_dedupe`` DEPENDS on it, and an adversarial review
        # is why.**  That index prevents the double-submit that silently doubles a
        # projected debit and credit (F-050 / C-22), and its key includes
        # ``amount``.  PostgreSQL indexes NULLs as DISTINCT by default, so two
        # ad-hoc transfers with a NULL amount and otherwise identical keys would
        # BOTH insert -- the guard disabled by a column the amount model made
        # nullable.  The first draft of the comment above asserted the index was
        # unaffected "because an ad-hoc transfer owns its amount", which was true
        # and enforced by nothing; this is that sentence made structural.
        db.CheckConstraint(
            "amount_source_id IS NULL OR transfer_template_id IS NOT NULL",
            name="ck_transfers_adhoc_owns_amount",
        ),
        # THE SETTLED FIGURE'S TWO STORAGE RULES (plan step
        # ``balance:X-bi-6-4d-2``, rulings **R-BAL365** and **R-BAL366**).
        # The figure is a MAGNITUDE -- a settle verb takes one, and each
        # side's direction is its own -- and ``$0.00`` is storable: a close
        # of nothing stores ``0.00`` and who typed it until plan step
        # ``balance:X-db`` refuses a new one at every door and tightens this
        # to ``> 0``.  The pair is stored whole or not at all, written as an
        # equality of two NULL tests (``ck_transfers_amount_ownership``'s
        # form); :class:`~app.models.settled_figure.SettledFigure` makes the
        # half pair unconstructible, and this is its backstop for a writer
        # that is not this application.
        db.CheckConstraint(
            "settled_figure >= 0",
            name="ck_transfers_settled_figure_non_negative",
        ),
        db.CheckConstraint(
            "(settled_figure IS NULL) = (settled_figure_source_id IS NULL)",
            name="ck_transfers_settled_figure_source_pairing",
        ),
        # A TRANSFER OF A DEFINITION IS DATED (plan step **X-bv-2**, ruling
        # **R-BAL17**, finding **BAL-463**): the twin of
        # ``ck_transactions_template_row_needs_due_date`` in
        # :mod:`app.models._transaction_table_args`, which carries the full
        # argument.  Amount rule 3 prices a derived transfer from its
        # definition's series *as of its own due date*, and ruling **D5**
        # forbids the pay period's bounds as a substitute -- so a linked
        # transfer with no date is unpriceable, and the arm of
        # ``cash_ledger._definition_cash._stated_amount`` that refused one is
        # deleted with this constraint rather than kept as a fence.  Every
        # constructor that sets ``transfer_template_id`` dates the row: the
        # recurrence engine splats ``DerivedTransferFields`` (whose date is
        # ``compute_due_date``'s answer, never ``None``) and the one-time
        # branch of ``routes/transfers/_instances`` writes the chosen
        # paycheck's start.  The one PATCH door left that could clear the date
        # on a linked transfer refuses it first
        # (``routes/transfers/mutations._reject_generated_due_date_edit``,
        # reading ``recurs`` and ``is_placed`` since plan step X-ci-1); this is
        # its backstop for a writer that is not the application.
        # An AD-HOC transfer is untouched: it owns its figure, nothing prices
        # it by its date, and the form still offers the field.  Measured
        # before binding: 0 of 177 linked transfers undated on production,
        # 2026-09-12.  Migration ``4d7123cd9803``.
        db.CheckConstraint(
            "transfer_template_id IS NULL OR due_date IS NOT NULL",
            name="ck_transfers_template_row_needs_due_date",
        ),
        # WHAT A GENERATED ROW IS, stated as storage (plan step **R17**).  A
        # row answers ONE occurrence of its template's cadence; the pay period
        # is where that occurrence's money lands, which is a DERIVED placement
        # and not the row's identity -- the owner may move it, and moving it is
        # exactly what ledger row **D57** was.  Keyed on the paycheck, this
        # index made a moved row vacate its own occurrence, so the next
        # generate pass answered it a second time: 8 rows, $1,482.93, measured
        # on a production clone 2026-08-28.
        #
        # TWO indexes rather than one, because ``occurs_on`` is NULLABLE and
        # PostgreSQL treats NULLs as distinct -- a single index over it would
        # let a template hold unlimited undated rows in one paycheck, which is
        # the "one row per template per paycheck" rule this table has always
        # had.  A row that answers NO occurrence therefore keeps the OLD key,
        # and that split is the same rule
        # ``_recurrence_common.OccurrenceClaims`` applies in Python: identity is
        # the occurrence where it is known, and the paycheck where it is not.
        # Letting an undated row claim nothing was measured at 41 phantom
        # transfers / $20,500 at the unarchive door.
        #
        # **The two predicates DIVERGED at plan step X-au-h**, exactly as the
        # transaction twin did -- see ``models/_transaction_table_args.py`` and
        # migration ``e7c3a1f9b482`` for the full
        # argument, the measurement, and why the UNDATED index below keeps the
        # exemption this one drops.  In one line: the exemption guarded a
        # PAYCHECK-keyed collision, R17 re-keyed this index onto ``occurs_on``,
        # a move does not change a row's occurrence, and X-au-h made a
        # RE-PRICE raise the flag -- so keeping it here would have dropped
        # re-priced rows out of a guarantee they never used to lose.
        # Ruling **R-JR**, developer 2026-09-03.
        db.Index(
            "idx_transfers_template_scenario_occurrence",
            "transfer_template_id", "scenario_id", "occurs_on",
            unique=True,
            postgresql_where=db.text(
                "transfer_template_id IS NOT NULL "
                "AND occurs_on IS NOT NULL "
                "AND is_deleted = FALSE"
            ),
        ),
        db.Index(
            "idx_transfers_template_scenario_undated",
            "transfer_template_id", "scenario_id", "pay_period_id",
            unique=True,
            postgresql_where=db.text(
                "transfer_template_id IS NOT NULL "
                "AND occurs_on IS NULL "
                "AND is_deleted = FALSE "
                "AND is_override = FALSE"
            ),
        ),
        # The SUPERKEYS a transfer side's payment names to prove its account is
        # that side's endpoint (``fk_transaction_entries_expense_side`` /
        # ``fk_transaction_entries_income_side``, plan step
        # ``balance:X-bi-6-4d-2``, ruling **R-BAL88**).  They constrain nothing
        # -- ``id`` is already the primary key -- and exist only because
        # PostgreSQL requires a UNIQUE over exactly the referenced columns
        # before a composite foreign key may target them: the construction
        # ``uq_transactions_id_account`` uses one table over.
        db.UniqueConstraint(
            "id", "from_account_id", name="uq_transfers_id_from_account",
        ),
        db.UniqueConstraint(
            "id", "to_account_id", name="uq_transfers_id_to_account",
        ),
        # **A TRANSFER'S ACCOUNTS ARE ITS OWNER'S** (plan step
        # ``balance:X-bi-6-4d-2``, ruling **R-BAL107**): each endpoint keys,
        # with ``user_id``, onto ``uq_accounts_id_user``, exactly as
        # ``fk_transactions_owner_account`` does for a row.  A transfer naming
        # another user's account is unstorable, and a side's payment, whose
        # account the side keys hold to an endpoint, is the owner's by that
        # chain.  ``ON DELETE RESTRICT`` matches the single-column endpoint keys
        # beside them, which stay as the ``from_account`` / ``to_account``
        # relationships' declared join paths.
        db.ForeignKeyConstraint(
            ["from_account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_transfers_owner_from_account",
            ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["to_account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_transfers_owner_to_account",
            ondelete="RESTRICT",
        ),
        # Ad-hoc duplicate prevention (F-050 / C-22).  Without this index
        # a double-submit of the ad-hoc transfer form -- network retry,
        # double-click, browser back-and-resubmit -- creates two parent
        # transfers in the same period.  Each duplicate transfer is a
        # second debit and a second credit (with two more shadow
        # transactions, until plan step ``balance:X-bi-6-4d-2`` deleted
        # the shadows), so a single accidental double-click silently
        # doubles the user's projected debit and credit; balance
        # projections drift by ``2 * amount`` until the user notices and
        # manually reconciles.
        # The composite key (user_id, from_account_id, to_account_id,
        # amount, pay_period_id, scenario_id) plus the
        # ``transfer_template_id IS NULL`` predicate scopes the
        # constraint to ad-hoc transfers only -- recurring transfers
        # are protected by the index above and may legitimately repeat
        # across periods.  ``is_deleted = FALSE`` keeps soft-deleted
        # rows out of the index so a delete-and-recreate workflow
        # remains legal, mirroring the predicate on
        # ``uq_transactions_credit_payback_unique``.  scenario_id is
        # included so an ad-hoc transfer in the baseline scenario
        # does not block the same transfer in a what-if scenario.
        db.Index(
            "uq_transfers_adhoc_dedupe",
            "user_id", "from_account_id", "to_account_id",
            "amount", "pay_period_id", "scenario_id",
            unique=True,
            postgresql_where=db.text(
                "transfer_template_id IS NULL "
                "AND is_deleted = FALSE"
            ),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    from_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # F-136 / C-43: ondelete=CASCADE replaces the historical RESTRICT
    # so the FK matches the sibling tables (``budget.transactions``
    # and ``budget.account_anchor_history`` both CASCADE on
    # ``pay_period_id``).  The asymmetry was an unintentional drift:
    # PostgreSQL evaluates every referential action for a single
    # DELETE in one pass, so a user-cascade that fans out into
    # ``pay_periods`` and ``transfers`` simultaneously would
    # previously have raised a RESTRICT error even though every
    # row was destined for deletion.  Until plan step
    # ``balance:X-bi-6-4d-2`` deleted the shadows, CASCADE also kept
    # a transfer with its two shadow transactions: the three and
    # their pay period disappeared together rather than leaving the
    # parent transfer orphaned after the shadows cascaded away
    # through ``transactions.pay_period_id``.
    # Name follows the SHEKEL_NAMING_CONVENTION (see
    # app/extensions.py).
    pay_period_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.pay_periods.id",
            name="fk_transfers_pay_period_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # Pylint: ``duplicate-code`` -- Incidental scenario_id + status_id FK
    # pair, shared by structure (not by domain) with the transaction table
    # -- both are budget events living in a scenario with a status.  They
    # are deliberately separate tables (a transfer moves money between two
    # accounts, a row is one account's), so a shared base would couple them
    # wrongly (coding-standards rule 13).  One-sided disable: the transaction
    # block stays un-disabled.
    # pylint: disable=duplicate-code
    scenario_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("ref.statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transfer_template_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transfer_templates.id", ondelete="SET NULL"),
    )
    name = db.Column(db.String(200))
    # The transfer's OWN amount, and NULLABLE since plan step X-au-c1: a transfer
    # whose amount is DERIVED does not store one (ruling **R-FI**).  NULL means
    # "ask ``cash_ledger.resolve_transfer_amount``", which for a generated
    # transfer is its definition's effective-dated price series as of the
    # transfer's own due date, and for a loan payment (a definition carrying
    # loan-payment settings) amount rule 4: the contract's cash in derive mode
    # or the series in manual mode, each plus the standing extra principal.
    # No production row was NULL at that step.  Plan step X-au-f emptied it
    # for every transfer of a definition its owner had not overridden
    # (migration ``b7e4c1f38a20``), and both creators of a transfer of a
    # definition write it derived since
    # (``transfer_recurrence._create_from_definition``, from
    # ``DerivedTransferFields``, and the one-time branch of
    # ``routes/transfers/_instances``); an ad-hoc transfer owns its figure.
    #
    # **PRIVATE since plan step X-au-k**, with the SQL name unchanged: read it
    # through :attr:`amount`, write it through :attr:`amount_ownership`.  The
    # transaction twin (``Transaction._estimated_amount``) states the argument
    # in full.
    #
    # **The name is DOUBLE-underscored, and that is the seal rather than a
    # style**: Python mangles it to ``_Transfer__amount``, so the single-underscore
    # spelling a reader would guess -- ``row._amount`` -- binds a plain
    # instance attribute that reaches no column at all.  A write that misses
    # the seam is then a no-op the next read exposes, instead of the
    # half-written pair this step exists to make unrepresentable.
    __amount = db.Column("amount", db.Numeric(12, 2))
    # WHICH RELATION prices this transfer, or NULL when it owns its own figure
    # (ruling **R-FI**, plan step X-au-c1).  Only ``template`` is meaningful here
    # -- a transfer has no parent transfer -- and RESTRICT is for the reason the
    # transaction twin states: a vanishing ref row would convert a derived
    # transfer into one claiming to own an amount it does not have.
    #
    # **PRIVATE since plan step X-au-k**, for the reason its partner states,
    # and double-underscored for the same seal.
    __amount_source_id = db.Column(
        "amount_source_id",
        db.Integer,
        db.ForeignKey(
            "ref.amount_sources.id",
            name="fk_transfers_amount_source_id",
            ondelete="RESTRICT",
        ),
    )
    # THE PAIR ABOVE, AS ONE ATTRIBUTE (plan step **X-au-k**), and the twin of
    # ``Transaction.amount_ownership`` -- see that attribute for why the pair
    # is one assignment and why ``ck_transfers_amount_ownership`` stays.  The
    # attribute carries the SAME name on both models even though the figure
    # COLUMN does not, which is what let the write seam stop dispatching on the
    # model to find out which column a table stores a figure in.
    amount_ownership = db.composite(from_columns, __amount, __amount_source_id)
    # WHAT THIS TRANSFER'S MONEY MOVED once it settled, and who wrote the
    # figure (plan step ``balance:X-bi-6-4d-2``, rulings **R-BAL365**,
    # **R-BAL366**): ONE figure for the transfer, where until this step each
    # side's payment record carried a copy.  NULLABLE: NULL is "no figure
    # stored" -- a transfer that has never settled, or whose revert of a
    # ``$0.00`` close cleared it.  KEPT across a revert of a close that moved
    # money, as the records are (ruling **R-BAL61**), so a re-settle honours
    # a figure somebody stated; released when the delete takes the records
    # off.  Written by the status seam's Transfer arm alone
    # (:mod:`app.services.status_seam._side`).
    #
    # PRIVATE and double-underscored, for the seal ``__amount`` states: read
    # through :attr:`settled_figure`, write through
    # :attr:`settled_figure_pair`.
    __settled_figure = db.Column("settled_figure", db.Numeric(12, 2))
    # WHO WROTE the settled figure: the ``ref.movement_figure_sources`` row
    # (``resolved`` / ``typed`` / ``observed``), RESTRICT for the reason
    # ``transaction_entries.figure_source_id`` states -- a vanishing
    # catalogue row would leave a figure with no source.  NULL exactly when
    # the figure is (``ck_transfers_settled_figure_source_pairing``).
    __settled_figure_source_id = db.Column(
        "settled_figure_source_id",
        db.Integer,
        db.ForeignKey(
            "ref.movement_figure_sources.id",
            name="fk_transfers_settled_figure_source_id",
            ondelete="RESTRICT",
        ),
    )
    # THE PAIR ABOVE, AS ONE ATTRIBUTE (the X-au-k seal
    # :attr:`amount_ownership` uses): a writer assigns a
    # :class:`~app.models.settled_figure.SettledFigure` or ``None``, never one
    # column.
    settled_figure_pair = db.composite(
        settled_figure_from_columns,
        __settled_figure, __settled_figure_source_id,
    )
    # is_override and is_deleted are provided by SoftDeleteOverridableMixin.
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="SET NULL"),
    )
    notes = db.Column(db.Text)
    # Calendar date the transfer is due.  Nullable, matching
    # ``Transaction.due_date`` (ad-hoc transfers may omit it; a generated
    # transfer's is ``recurrence.compute_due_date``'s answer).  Each side's leg reads
    # it here (``TransferLeg.due_date``, :mod:`app.services.transfer_legs`).
    # Until plan step ``balance:X-bi-6-4d-2`` deleted the shadows, the
    # transfer service mirrored it to both shadow transactions, and the
    # consumers of a due date read the shadow ``Transaction.due_date``.
    #
    # On a LOAN PAYMENT the ``due_date`` is a POSTING INPUT, not
    # display metadata: it is the INSTALLMENT the payment satisfies (the loan
    # engine reads it via ``loan_loaders.loan_payment_due_date`` rather than
    # re-deriving it from the pay period, which is wrong for a payment settled
    # late), and the genesis write walk orders payments by it and applies its
    # strict ``anchor_date < due_date`` post-anchor boundary against it.  Moving
    # it therefore changes the POSTED balance, so ``due_date`` is in
    # ``transfer_service._POSTING_RELEVANT_FIELDS`` and any other writer of this
    # column MUST follow it with a posting reconcile.
    due_date = db.Column(db.Date, nullable=True)
    # WHICH OCCURRENCE this transfer is.  A GENERATED transfer's is the date
    # its template's cadence named when the transfer recurrence engine wrote
    # it: the parallel of ``Transaction.occurs_on``, which carries the full
    # statement; both engines write it from the same
    # ``PlannedOccurrence.occurrence`` and the shared skip predicate reads it
    # identically, so a divergence here would be the drift
    # ``_recurrence_common`` exists to prevent (plan step **R17**).  A transfer
    # the one-time branch of ``routes/transfers/_instances`` PLACES answers its
    # own due date: it writes the two equal at birth, and ``transfer_service``
    # writes ``occurs_on`` beside a placed transfer's due date whenever that
    # date moves (ruling **R-BAL94**).  Two placed shapes differ until their
    # date first moves: a cleared cadence's survivor keeps the occurrence its
    # cadence named (``transfer_service/_update.py``'s due-date arm), and the
    # two one-time transfers born before plan step ``balance:X-ci-1`` answer
    # none until ``X-ci-3``'s backfill.  An ad-hoc transfer answers no
    # occurrence (NULL).
    #
    # **It was never mirrored onto the two shadow transactions** plan step
    # ``balance:X-bi-6-4d-2`` deleted: a shadow was not the rule's own row
    # (``transfer_service`` created it from the parent, never the recurrence
    # engine from an occurrence), and no generate pass asked a shadow whether
    # an occurrence had been written.
    occurs_on = db.Column(db.Date, nullable=True)
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    template = db.relationship("TransferTemplate", back_populates="transfers")
    from_account = db.relationship(
        "Account", foreign_keys=[from_account_id], lazy="joined"
    )
    to_account = db.relationship(
        "Account", foreign_keys=[to_account_id], lazy="joined"
    )
    status = db.relationship("Status", lazy="joined")
    pay_period = db.relationship("PayPeriod")
    scenario = db.relationship("Scenario")
    category = db.relationship("Category", lazy="joined")
    # **EACH SIDE'S PAYMENT RECORD, and a delete never takes one** (plan step
    # ``balance:X-bi-6-4d-2``, ruling **R-BAL88**; ``Transaction.entries``'
    # shape, rulings **R-CC54** and **R-CC64**).  At most one per side
    # (``uq_transaction_entries_one_expense_side_record`` /
    # ``..._one_income_side_record``): where and when the from-side's money
    # left and the to-side's arrived -- never how much, which the transfer
    # stores once (:attr:`settled_figure`) -- dated while the transfer is
    # settled and kept un-dated across a revert (ruling **R-BAL61**).  No
    # ``delete`` cascade and ``passive_deletes="all"``: the side keys are
    # ``NO ACTION``, so deleting a
    # transfer still holding a record is REFUSED at flush, and the one act that
    # takes a movement off the books (:mod:`app.services.movement_removal`)
    # deletes each record itself and takes it out of THIS list
    # (``transfer_legs.parent_entries``).  Written by the status seam's
    # Transfer arm (:mod:`app.services.status_seam._side`); read through
    # :mod:`app.services.transfer_legs`.
    expense_movements = db.relationship(
        "TransactionEntry",
        primaryjoin="Transfer.id == TransactionEntry.expense_transfer_id",
        foreign_keys="TransactionEntry.expense_transfer_id",
        back_populates="expense_transfer",
        lazy="select",
        cascade="save-update, merge, refresh-expire, expunge",
        passive_deletes="all",
        order_by="TransactionEntry.id",
    )
    income_movements = db.relationship(
        "TransactionEntry",
        primaryjoin="Transfer.id == TransactionEntry.income_transfer_id",
        foreign_keys="TransactionEntry.income_transfer_id",
        back_populates="income_transfer",
        lazy="select",
        cascade="save-update, merge, refresh-expire, expunge",
        passive_deletes="all",
        order_by="TransactionEntry.id",
    )

    @hybrid_property
    def amount(self):
        """Return the figure this transfer states as its own, or ``None``.

        The read-only projection of :attr:`amount_ownership`, and the twin of
        ``Transaction.estimated_amount`` -- see it for why there is no setter
        and why this is a hybrid rather than a plain property.  To state this
        transfer's amount, assign :attr:`amount_ownership` through
        ``app.services.amount_ownership``.

        Returns:
            The stored figure, or ``None`` when this transfer's amount is
            derived or not yet stated.
        """
        return self.__amount

    @hybrid_property
    def amount_source_id(self):
        """Return the id of the relation pricing this transfer, or ``None``.

        The read-only twin of :attr:`amount`.  ``None`` means the transfer
        owns its figure, which is the NULL test
        ``ck_transfers_amount_ownership`` is written over.

        Returns:
            The ``ref.amount_sources`` id, or ``None`` when the transfer owns
            its amount or has not stated its ownership yet.
        """
        return self.__amount_source_id

    @hybrid_property
    def settled_figure(self):
        """Return what this transfer's money moved once it settled, or ``None``.

        The read-only projection of :attr:`settled_figure_pair`, a hybrid for
        the reason :attr:`amount` is one: a query selects it by the class
        name (``pay_period_locks._movement_arms``).

        Returns:
            The stored figure (``0.00`` for a close of nothing, ruling
            **R-BAL365**), or ``None`` when no figure is stored.
        """
        return self.__settled_figure

    @hybrid_property
    def settled_figure_source_id(self):
        """Return the ``ref.movement_figure_sources`` id of who wrote the figure.

        The read-only twin of :attr:`settled_figure`.

        Returns:
            The source id, or ``None`` exactly when no figure is stored.
        """
        return self.__settled_figure_source_id

    @property
    def purchases(self) -> tuple[()]:
        """``()``: a transfer holds no purchase.

        What ``Transaction.purchases`` answers for a plan row -- what a person
        recorded against it, less the status seam's covering movement --
        asked of the other parent (ruling **plan#1983**, "price the parent,
        draw the side").  A movement filed under a transfer is a side's
        RECORD (``ck_transaction_entries_side_link_is_a_record``), never a
        purchase, so the answer is the schema's.  It is asked by the rules
        that price a parent whatever its table: whether a settle books a
        parent's purchases (``transaction_service.settles_from_entries``) and
        what the plan reserves for them (``cash_ledger._entry_aware_amount``);
        a side's ``purchases`` reads it off its transfer.
        """
        return ()

    @DerivedFlag
    def recurs(self):
        """True when this transfer's definition has a recurrence rule.

        **The ONE accessor for "is there a cadence to derive this transfer
        from"** (plan step ``balance:X-ci-1``, the twin of
        :attr:`Transaction.recurs` under ruling **R-BAL20**): it delegates to
        :attr:`TransferTemplate.recurs` and is what every site that used to
        read ``transfer_template_id`` as *generated* reads instead -- the
        override flip on a typed figure or a period move
        (``routes/transfers/mutations``), carry-forward's transfer move, the
        due-date gate and the discardable count.  A transfer of a RULE-LESS
        definition -- a one-time transfer, or a cleared cadence's survivor --
        answers ``False`` here while its link is set, so those sites treat it
        as the one-time transfer it is (:attr:`is_placed`): it takes a due
        date, moves without a flag, and is counted unrecoverable.  An ad-hoc
        transfer (no definition at all, the shape ``X-ci-3`` makes
        unrepresentable) answers ``False`` too, as it always did.

        Reads the template relationship only when ``transfer_template_id``
        is set, so a link-less row costs no load; the rule rides on the
        template's own joined load.  A :class:`DerivedFlag` for the reason
        its neighbours are: the answer lives on the rule's table, and a
        query keyed on the class-level name refuses to build rather than
        restating that rule in SQL -- ``pay_period_gates
        .count_discardable_items`` loads and asks (finding **BAL-492**).

        **Until this leaf the due-date gate read a property of its own,
        ``due_date_is_its_definitions``**, kept on the row because the
        transfer had TWO edit doors (finding BAL-476); leaf
        ``balance:X-bi-6-1`` deleted the second, and a property that
        returned another property for one caller was two spellings of one
        rule, so the gate reads this directly as its transaction twin does.
        """
        if self.transfer_template_id is None:
            return False
        return self.template.recurs

    @DerivedFlag
    def is_placed(self):
        """True when this transfer was PLACED by a rule-less definition.

        **The ONE accessor for "is this a one-time transfer"** (plan step
        ``balance:X-ci-1``, the twin of :attr:`Transaction.is_placed`): a
        definition with no recurrence rule PLACES its one transfer
        (``routes/transfers/_instances._materialize_one_time_transfer``, in
        the paycheck the owner chose, due on its start) where a definition
        with a rule GENERATES them, and an ad-hoc transfer names no
        definition at all.  Three doors fork on exactly this until ``X-ci-3``
        deletes the ad-hoc shape: the PATCH's due-date gate lets such a
        transfer's date MOVE but not CLEAR (an ad-hoc one may still clear it,
        the developer-ratified behaviour the ad-hoc door keeps until it
        goes), the same PATCH restates its definition's price on a typed
        figure (**R-BAL92**) where an ad-hoc transfer takes the figure as its
        own, and the full-edit form renders its date input ``required``.
        The service door re-places its date on a period move (**R-BAL93**)
        and keeps ``occurs_on`` equal to it (**R-BAL94**) by asking this.

        Once ``transfer_template_id`` is NOT NULL (``X-ci-3``) this is
        ``not recurs`` for every row; it stays named because the doors ask
        *is this a one-time transfer*, not *is this not recurring*, and the
        transaction twin keeps both names for the same reason.
        """
        if self.transfer_template_id is None:
            return False
        return not self.template.recurs

    def __repr__(self):
        return f"<Transfer '{self.name}' ${self.amount} ({self.id})>"
