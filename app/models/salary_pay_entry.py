"""
Shekel Budget App -- Salary Pay Entry Model (salary schema)

What ONE paycheck pays from a dated payday on: the salary's stored fact since
plan step salary:X-av-3a.
"""

from app.extensions import db
from app.models.mixins import OptimisticLockMixin, TimestampMixin


class SalaryPayEntry(OptimisticLockMixin, TimestampMixin, db.Model):
    """One entry of a salary profile's pay list: from *payday* on, *amount* a paycheck.

    **The salary's one stored fact** (plan step salary:X-av-3a, rulings
    **R-SAL59** and **R-SAL61**; findings **N-237** and **N-391**).  It
    replaced ``salary_profiles.annual_salary``, one undated yearly figure the
    engine divided by the paychecks a year: a typed-in raise re-priced every
    paycheck not yet received, back to January, and a typo fix looked exactly
    like a raise.  An entry is dated, so a raise received is a NEW entry and a
    correction is a Fix of the entry it corrects, which moves only the
    paychecks that entry covers.  The yearly figure is the amount times the
    paychecks a year of the rhythm in force on the payday, shown and never
    stored (**R-SAL59**, **R-SAL66**).

    What a payday is priced at is
    :meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`'s answer, and
    this row is read nowhere else by the engine: the latest entry on or before
    the payday (else the first), then every forecast raise landing after that
    entry's payday (:func:`~app.services.salary_raises.applications_between`,
    each step rounded by :func:`~app.services.salary_raises.raise_pay`).

    A profile holds at least one entry, and every door keeps it so: the
    create form writes the first, and no door removes the last (**R-SAL68**;
    the Remove door is plan step salary:X-av-3b's).

    Optimistic locking: see :class:`Transaction` for the ``version_id_col``
    contract.  Two tabs fixing one entry race for the bump; the loser raises
    ``StaleDataError`` and the route surfaces a flash + redirect.

    Attributes:
        payday: The payday the entry takes effect on -- a payday on the
            owner's calendar, saved or projected, which the doors check.
        amount: The gross base pay of one paycheck from that payday on,
            before any forecast raise landing after it.
    """

    __tablename__ = "pay_entries"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_pay_entries_positive_amount"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_pay_entries_version_id_positive",
        ),
        # One entry per payday per profile: "from payday X" names one pay, so
        # a second entry on the same payday would be two answers to one
        # question with nothing to choose between them.  The leading column
        # also serves the engine's per-profile load, so no separate index.
        db.UniqueConstraint(
            "salary_profile_id", "payday",
            name="uq_pay_entries_profile_payday",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    payday = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    # Declared here rather than taken from SalaryProfileScopedMixin, whose
    # key takes PostgreSQL's default name: migration 70680a4a7405 named this
    # one, and production holds that name (plan#1858).  Last of the class's
    # own columns, where the mixin's rendered, so the models emit the table's
    # columns in the order they did.
    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "salary.salary_profiles.id",
            name="fk_pay_entries_salary_profile_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    salary_profile = db.relationship(
        "SalaryProfile", back_populates="pay_entries",
    )

    def __repr__(self):
        return f"<SalaryPayEntry {self.payday} ${self.amount}>"
