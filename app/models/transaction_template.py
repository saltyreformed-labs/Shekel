"""
Shekel Budget App -- Transaction Template Model (budget schema)

A template defines a recurring income or expense (e.g. "Car Payment")
along with its recurrence rule and its price: a dated series
(``amount_versions``), or the paycheck a salary profile works out.  The
recurrence engine uses templates to auto-generate Transaction rows into
future pay periods.
"""

from app.extensions import db
from app.models._derived_flag import DerivedFlag
from app.models.mixins import (
    IsActiveMixin,
    OptimisticLockMixin,
    SortOrderMixin,
    TimestampMixin,
    UserScopedMixin,
)


class TransactionTemplate(
    UserScopedMixin, IsActiveMixin, SortOrderMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
    """Blueprint for a recurring income or expense line item.

    Optimistic locking: ``version_id`` is the SQLAlchemy
    ``version_id_col`` for the row, narrowing every ORM UPDATE/DELETE
    with ``WHERE id = ? AND version_id = ?`` and atomically
    incrementing the counter.  Concurrent template edits (e.g. two
    tabs both renaming it) race for the bump; the
    loser raises :class:`sqlalchemy.orm.exc.StaleDataError`, which
    the route converts into a flash + redirect.  See commit C-18 of
    the 2026-04-15 security remediation plan.
    """

    __tablename__ = "transaction_templates"
    __table_args__ = (
        # The SUPERKEY ``fk_merchant_rules_template_account`` targets, so
        # a merchant rule's template is provably on the policy's
        # own ACCOUNT (plan step ``bank_import:X-f6a-3d``).  It constrains
        # nothing -- ``id`` is already the primary key -- and exists only
        # because PostgreSQL requires a UNIQUE over exactly the referenced
        # columns before a composite foreign key may target them.  The same
        # construction, for the same reason, as ``uq_accounts_id_user``.
        # **Declared HERE as well as in the migration**: a constraint the
        # migration creates and the model does not know about is one the next
        # ``flask db migrate`` emits a DROP for.
        db.UniqueConstraint(
            "id", "account_id", name="uq_transaction_templates_id_account",
        ),
        db.Index("idx_templates_user_type", "user_id", "transaction_type_id"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transaction_templates_version_id_positive",
        ),
        {"schema": "budget"},
    )

    # Pylint: ``duplicate-code`` -- Incidental id-PK + account FK preamble,
    # shared by structure (not by domain) with the transaction table.  A
    # transaction is a generated instance and a template is its blueprint;
    # they are deliberately separate tables, so extracting a base would couple
    # them wrongly (coding-standards rule 13).  One-sided disable.
    # pylint: disable=duplicate-code
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # NULLABLE since plan step ``balance:X-bi-7b`` (ruling **R-BAL24**), for
    # ONE producer: ``statement_match._uncategorized.mint_uncategorized`` mints
    # a definition for the row a bank line's money requires, and two of its
    # three callers state no category -- the app does not know what that
    # money was, and saying so is what books its counter leg to the per-owner
    # Uncategorized LEDGER ACCOUNT (**R-FN**) rather than misfiling it.  Every
    # definition the template form or the grid mints carries one (their
    # schemas require it).  Migration ``9c1e4b7a2d3f``; its downgrade
    # re-adds ``NOT NULL`` and REFUSES while a category-less definition exists.
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="RESTRICT"),
        nullable=True,
    )
    transaction_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.transaction_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(200), nullable=False)
    # No price column: a definition's price is its dated series
    # (``amount_versions``), or a computation for a salary profile's
    # definition.  ``default_amount`` stored a copy of the newest version
    # until plan step X-bp dropped it (rulings R-IY, R-BAL16).
    # Whether the rows this definition generates take PURCHASE ENTRIES -- the
    # "envelope" mode, where a row accumulates per-purchase line items rather
    # than settling as one figure.  This is the DEFINITION's setting and the
    # one answer for every row it generates: ``Transaction.tracks_purchases``
    # reads it here for a template-generated row and never that row's own
    # cell (ruling **R-JQ**).
    #
    # Declared inline rather than through the mixin since plan step
    # ``balance:X-bi-1``, because the two tables stopped treating the column
    # alike: here it is public and settable, on ``Transaction`` it is sealed.
    # The column declaration is identical to the mixin's it replaces, so an
    # autogenerate diff against a migrated schema stays empty (measured); its
    # position in ``CREATE TABLE`` moves off the tail, which is load-bearing
    # nowhere here -- see ``UserScopedMixin`` for the standard.
    is_envelope = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )
    # WHETHER A COMPANION OF THE OWNER MAY SEE the rows this definition
    # generates -- the definition's own setting, read for every one of them
    # by ``Transaction.visible_to_companion`` and never that row's own cell.
    # Declared inline since plan step ``balance:X-bi-1b`` for the reason
    # ``is_envelope`` is, one step earlier: the twin cell on ``Transaction``
    # took the same seal, so the two tables stopped treating the column alike
    # and the mixin they shared (``CompanionVisibilityMixin``, once
    # ``TrackingVisibilityMixin``) had one consumer left and dissolved.  The
    # declaration is identical to the mixin's; only its position in
    # ``CREATE TABLE`` moves, load-bearing nowhere here.
    companion_visible = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )
    # is_active + sort_order: from IsActiveMixin / SortOrderMixin.
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    account = db.relationship("Account", lazy="joined")
    category = db.relationship("Category", lazy="joined")
    # 0-or-1 cadence, and the FK is on the RULE (plan step R-F6): a template
    # that does not repeat simply has no rule row, where it used to hold a NULL
    # in a ``recurrence_rule_id`` column.  ``uselist=False`` because
    # ``uq_recurrence_rules_transaction_template_id`` makes at most one possible;
    # ``lazy="joined"`` preserves the load this was doing from the other side,
    # so every reader of ``template.recurrence_rule`` still costs no second
    # query.  ``delete-orphan`` is what makes "this no longer repeats"
    # (``_recurrence_form_helpers._clear_recurrence_rule``) a disposal rather
    # than a detach -- and the DB FK is ``ON DELETE CASCADE`` besides, for the
    # hard-delete route's bulk path and for every writer that never sees this
    # mapper.  Exactly the pair ``amount_versions`` below already carries.
    recurrence_rule = db.relationship(
        "RecurrenceRule", uselist=False, lazy="joined",
        cascade="all, delete-orphan",
        back_populates="transaction_template",
    )
    transaction_type = db.relationship("TransactionType", lazy="joined")
    transactions = db.relationship(
        "Transaction", back_populates="template", lazy="select"
    )
    # The effective-dated history of the definition's price (plan step
    # X-au-a), its only price since X-bp.  A
    # salary-linked template has NONE and must not: the paycheck calculator
    # prices its rows, so a version here would record a figure nobody stated.
    # :mod:`app.services.template_amount_service` owns both the predicate and
    # the write door; ``delete-orphan`` disposes the history with the template
    # (the DB FK is ON DELETE CASCADE too, for the hard-delete route's bulk
    # path).
    amount_versions = db.relationship(
        "TemplateAmountVersion",
        back_populates="transaction_template",
        cascade="all, delete-orphan",
        lazy="select",
    )

    @DerivedFlag
    def recurs(self):
        """True when this definition has a recurrence rule.

        **The ONE accessor for "does this definition repeat"** (plan step
        ``balance:X-bi-7a``, ruling **R-BAL20**), stated on the definition
        because that is where the fact lives -- the 0-or-1 cadence
        ``recurrence:R-F6`` put on the RULE's side -- and delegated to by
        :attr:`Transaction.recurs` for every row it generated.  A definition
        with no rule is a ONE-OFF's: its rows are PLACED rather than
        generated (one per one-off since the family's doors and cutover
        leaves, ``X-bi-7b`` and ``X-bi-7d-2``; a cleared cadence's
        survivors too), no pass ever regenerates
        them, the account-delete refusal counts these out, and
        ``recurrence_engine.propagate_to_unruled_definition`` is how its
        edits reach its rows.

        A :class:`DerivedFlag` rather than a plain property because the
        question is one a query would want to ask -- *the recurring
        definitions on this account* -- and a plain property at class level
        compares ``False`` to everything, so ``filter_by(recurs=True)`` would
        return no rows with no error.  Spelling it in SQL (``has()`` over the
        rule) would be a second body of one rule (ruling **R-IZ**); the
        readers load and ask.
        """
        return self.recurrence_rule is not None

    def __repr__(self):
        return f"<TransactionTemplate {self.id} '{self.name}'>"
