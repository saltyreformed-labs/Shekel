"""
Shekel Budget App -- Recurring-definition amount versions (budget schema)

The effective-dated price history of a recurring definition's amount.  A
transaction template and a transfer template each carried ONE stated amount
(``default_amount``), a scalar with no time dimension: when a subscription's
price rose the old figure was overwritten, so the app could not say what the
definition cost last March and a price change retroactively rewrote every
past projection.  :class:`TemplateAmountVersion` is that amount as a dated
SERIES instead, and since plan step X-bp dropped the scalar (rulings R-IY,
R-BAL16) the only place a price is stated -- rule 1 of ruling **R-FI**,
"every financial quantity that varies over time is a dated series resolved
as-of".

**Supersession, not ranges.**  A version is in effect from its
``effective_date`` until the NEXT version of the same template supersedes it,
so there is no ``end_date`` and two versions of one template can never
overlap -- the same model :class:`~app.models.escrow_line.EscrowComponentVersion`
uses for an escrow line, and for the same reason: an overlapping-range state
that has to be checked is a state that can exist, while an unrepresentable one
cannot.

**The date a version is compared against is the ROW's own due date**, not the
pay period that funds it (developer, 2026-08-11), which is the rule ruling D5
already applies to a loan payment's escrow: a bill's price is a fact about the
day the bill is due.  A recurring definition therefore prices a bill due
2026-09-12 at the version in effect on 2026-09-12, even when the paycheck
funding it runs past a later version's date.

**Before the earliest version the series HOLDS FLAT** -- the resolver
back-projects rather than answering "no amount", which is ruling **R-I**'s shape
on the balance fold applied here.  A template generates rows into historical pay
periods as readily as into future ones, so a partial resolver would refuse to
price a row the app itself created; the honest answer for a date before any
recorded price is the earliest price recorded.  See
:func:`app.services.template_amount_service.amount_as_of`.

**Which definitions have a series, and which must not.**  A version records an
amount somebody STATED.  A template whose amount is DERIVED gets no series at
all, because building a price history over a derived quantity manufactures a
history nobody ever stated: a salary-linked transaction template (the paycheck
calculator prices each row) and a derive-mode loan-payment transfer template
(amount rule 4 computes its P&I plus escrow from the loan) are both excluded.
:func:`app.services.template_amount_service.owns_its_amount` is the single
predicate, and :func:`app.services.template_amount_service.set_amount` is the
single write door.

**This table PRICES every generated transaction row, as of plan step
X-au-e.**  It was additive at X-au-a -- ``default_amount`` was authoritative
and nothing read this table -- then X-au-b built the resolver over it, and
X-au-e cut generated rows onto it: such a row stores no figure and is worth
what this series states on its own due date (amount rule 3,
``template_amount_service.amount_as_of``).  **Two consequences a reader needs.**
A definition with NO version here generates rows nothing can price, which is
why both create doors call ``set_amount`` immediately after the flush and why
the cutover migration refuses rather than declaring such a row.  And withdrawing
a version is a money-moving act unless its predecessor states the same amount,
which ``template_amount_service._may_withdraw`` is the one statement of.

A generated TRANSFER still stores its own amount; plan step X-au-f is what puts
that half onto this table too.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class TemplateAmountVersion(TimestampMixin, db.Model):
    """One effective-dated version of a recurring definition's stated amount.

    A child of EXACTLY ONE of :class:`~app.models.transaction_template.TransactionTemplate`
    or :class:`~app.models.transfer_template.TransferTemplate`, through an
    exclusive arc of two nullable typed foreign keys with a CHECK that exactly
    one is set (developer, 2026-08-11).  One table rather than two: the two
    template kinds are parallel by construction, so a version table per kind
    would duplicate the model, the resolver, the write door and their tests --
    the duplication this codebase already carries several one-sided
    ``duplicate-code`` disables for.  The arc is EXACTLY-one rather than
    at-most-one (which is what ruling R-DY chose for ``journal_entries``): a
    version with no owner is not a state this table has, because an amount
    nobody stated for anything is not an amount.

    ``uq_template_amount_versions_transaction_effective`` and its transfer twin
    are PARTIAL unique indexes -- at most one version per template per date, so
    a same-day correction edits that row rather than appending a second -- and
    each doubles as the index for the as-of lookup
    (``WHERE <fk> = ? AND effective_date <= ? ORDER BY effective_date DESC``).
    Partial rather than plain: half this table's rows carry ``NULL`` in either
    key, and an index over those NULLs answers no query.

    The two amount CHECKs mirror the owning tables' own rules rather than
    restating a looser one: ``ck_transaction_templates_nonneg_amount`` allows
    zero, ``ck_transfer_templates_positive_amount`` does not, so a transfer
    version is pinned strictly positive and a transaction version merely
    non-negative.  Stating it here as well as there is what stops a version
    from carrying a figure its own template could not.
    """

    __tablename__ = "template_amount_versions"
    __table_args__ = (
        # The exclusive arc: exactly one owner.  ``<>`` on two NULL tests is
        # XOR, so both-set and neither-set are equally refused.
        db.CheckConstraint(
            "(transaction_template_id IS NULL) <> (transfer_template_id IS NULL)",
            name="ck_template_amount_versions_one_owner",
        ),
        # A stated amount is never negative (the transaction template's own
        # floor); the storage-tier guard for raw-SQL writers.
        db.CheckConstraint(
            "amount >= 0",
            name="ck_template_amount_versions_nonneg_amount",
        ),
        # ...and a TRANSFER's is strictly positive, matching
        # ``ck_transfer_templates_positive_amount``: a transfer of $0.00 moves
        # no money and would produce two shadow legs that net to zero.
        db.CheckConstraint(
            "transfer_template_id IS NULL OR amount > 0",
            name="ck_template_amount_versions_transfer_positive_amount",
        ),
        # The window a stated date may fall in, mirroring the schema bound
        # (``schemas/validation/_helpers.EFFECTIVE_DATE_MIN`` / ``_MAX``) at the
        # storage tier.  An HTML date input accepts a four-digit-year typo and
        # the consequence here is permanent: a stray ``0202`` becomes the
        # series' EARLIEST version, which anchors every date before the series
        # and which the withdrawal door refuses to remove.
        db.CheckConstraint(
            "effective_date >= DATE '2000-01-01' "
            "AND effective_date <= DATE '2100-12-31'",
            name="ck_template_amount_versions_effective_date_range",
        ),
        # At most one version per template per date, per arm; also the as-of
        # index.  Partial so the half of the table carrying NULL in one key is
        # not indexed under it.
        db.Index(
            "uq_template_amount_versions_transaction_effective",
            "transaction_template_id", "effective_date",
            unique=True,
            postgresql_where=db.text("transaction_template_id IS NOT NULL"),
        ),
        db.Index(
            "uq_template_amount_versions_transfer_effective",
            "transfer_template_id", "effective_date",
            unique=True,
            postgresql_where=db.text("transfer_template_id IS NOT NULL"),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    transaction_template_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.transaction_templates.id", ondelete="CASCADE",
            name="fk_template_amount_versions_transaction_template_id",
        ),
        nullable=True,
    )
    transfer_template_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.transfer_templates.id", ondelete="CASCADE",
            name="fk_template_amount_versions_transfer_template_id",
        ),
        nullable=True,
    )
    effective_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)

    # Relationships
    transaction_template = db.relationship(
        "TransactionTemplate", back_populates="amount_versions",
    )
    transfer_template = db.relationship(
        "TransferTemplate", back_populates="amount_versions",
    )

    def __repr__(self):
        owner = (
            f"txn_template_id={self.transaction_template_id}"
            if self.transaction_template_id is not None
            else f"transfer_template_id={self.transfer_template_id}"
        )
        return (
            f"<TemplateAmountVersion id={self.id} {owner} "
            f"effective={self.effective_date} amount={self.amount}>"
        )
