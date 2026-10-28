"""
Shekel Budget App -- Tax Configuration Models (salary schema)

Models for federal tax brackets, state tax config, and FICA rates
used by the paycheck calculator to compute tax withholdings.
"""

from decimal import Decimal

from app.extensions import db
from app.models.mixins import CreatedAtMixin, SortOrderMixin, UserScopedMixin


class TaxBracketSet(UserScopedMixin, CreatedAtMixin, db.Model):
    """A set of federal income tax brackets for a specific year and filing status."""

    __tablename__ = "tax_bracket_sets"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "tax_year", "filing_status_id",
            name="uq_tax_bracket_sets_user_year_status",
        ),
        db.CheckConstraint("standard_deduction >= 0", name="ck_tax_bracket_sets_nonneg_deduction"),
        db.CheckConstraint(
            "child_credit_amount >= 0",
            name="ck_tax_bracket_sets_nonneg_child_credit",
        ),
        db.CheckConstraint(
            "other_dependent_credit_amount >= 0",
            name="ck_tax_bracket_sets_nonneg_other_credit",
        ),
        # Taxes slice T-P5: the refundable Additional Child Tax Credit
        # (ACTC) cap PER qualifying child.  A per-year federal constant
        # ($1,700 for 2025 and 2026 per IRS Rev. Proc. 2025-32 sec.
        # 4.05(2) / 2025 Schedule 8812 instructions) that bounds the
        # refundable portion of the CTC, so it lives on the year+status
        # bracket set alongside the nonrefundable credit amounts.
        db.CheckConstraint(
            "child_credit_refundable_cap >= 0",
            name="ck_tax_bracket_sets_nonneg_refundable_cap",
        ),
        # F-077 / C-24: ``tax_year`` is the IRS tax year a bracket
        # set applies to.  The schema layer added the same Range in
        # commit C-24; the CHECK is the storage-tier counterpart.
        db.CheckConstraint(
            "tax_year >= 2000 AND tax_year <= 2100",
            name="ck_tax_bracket_sets_valid_tax_year",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # F-073 / C-43: explicit ondelete=RESTRICT + fk_* name on the
    # filing-status ref-table FK.  See app/extensions.py for the
    # full SHEKEL_NAMING_CONVENTION rationale.
    filing_status_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.filing_statuses.id",
            name="fk_tax_bracket_sets_filing_status_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    tax_year = db.Column(db.Integer, nullable=False)
    standard_deduction = db.Column(db.Numeric(12, 2), nullable=False)
    # server_default uses the bare string "0" (not db.text("0")) so
    # pg_dump renders the default as 'DEFAULT '0'::numeric' -- matching
    # the form materialised by migration b4c7d8e9f012's
    # ``server_default='0'``.  db.text("0") would render as
    # ``DEFAULT 0`` (literal), functionally identical but a pg_dump
    # diff against the migration-built schema.
    child_credit_amount = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default="0",
    )  # Per qualifying child under 17
    other_dependent_credit_amount = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default="0",
    )  # Per other dependent
    # Refundable ACTC cap per qualifying child (Taxes slice T-P5).  Same
    # ``server_default="0"`` bare-string form as the credit columns above
    # so pg_dump matches the migration-built schema; the seed sets the
    # verified $1,700 explicitly and the migration backfills existing
    # per-user rows.  A bracket set that omits it degrades to a zero
    # refundable credit (never an inflated refund).
    child_credit_refundable_cap = db.Column(
        db.Numeric(12, 2), nullable=False, default=0,
        server_default="0",
    )  # Refundable ACTC cap per qualifying child
    description = db.Column(db.String(200))

    # Relationships
    filing_status = db.relationship("FilingStatus", lazy="joined")
    brackets = db.relationship(
        "TaxBracket", back_populates="bracket_set",
        cascade="all, delete-orphan", lazy="select",
        order_by="TaxBracket.sort_order",
    )

    def __repr__(self):
        return f"<TaxBracketSet year={self.tax_year} status_id={self.filing_status_id}>"


class TaxBracket(SortOrderMixin, db.Model):
    """A single tax bracket within a bracket set."""

    __tablename__ = "tax_brackets"
    __table_args__ = (
        db.CheckConstraint("min_income >= 0", name="ck_tax_brackets_nonneg_min"),
        db.CheckConstraint(
            "max_income IS NULL OR max_income >= min_income",
            name="ck_tax_brackets_income_order",
        ),
        db.CheckConstraint("rate >= 0 AND rate <= 1", name="ck_tax_brackets_valid_rate"),
        # F-071 / F-079 / C-42: child-FK index restored after the
        # 22b3dd9d9ed3 migration dropped it without restoration.  The
        # tax calculator queries
        # ``WHERE bracket_set_id = ? ORDER BY sort_order`` to
        # materialise the bracket ladder for a year + filing status;
        # without this composite index the ORDER BY requires a sort
        # step over a sequential scan and the cost grows with the
        # total bracket-row count across all bracket sets.
        db.Index(
            "idx_tax_brackets_bracket_set",
            "bracket_set_id", "sort_order",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    bracket_set_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.tax_bracket_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    min_income = db.Column(db.Numeric(12, 2), nullable=False)
    max_income = db.Column(db.Numeric(12, 2))
    rate = db.Column(db.Numeric(5, 4), nullable=False)
    # sort_order: from SortOrderMixin.

    # Relationships
    bracket_set = db.relationship("TaxBracketSet", back_populates="brackets")

    def __repr__(self):
        return f"<TaxBracket {self.rate} ({self.min_income}-{self.max_income})>"


class StateTaxConfig(UserScopedMixin, CreatedAtMixin, db.Model):
    """State-level tax configuration (flat rate or none), per year and filing status.

    Taxes slice T-P5 (finding 2b): the NC standard deduction is
    filing-status-specific (Single/MFS $12,750, MFJ $25,500, HoH $19,125
    per N.C.G.S. 105-153.5(a)(1)), so the config is keyed on
    ``(user, state, year, filing_status)`` -- one row per filing status --
    rather than the former status-blind ``(user, state, year)``.  The flat
    rate itself is status-independent; only ``standard_deduction`` varies.
    """

    __tablename__ = "state_tax_configs"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "state_code", "tax_year", "filing_status_id",
            name="uq_state_tax_configs_user_state_year_status",
        ),
        db.CheckConstraint(
            "flat_rate IS NULL OR (flat_rate >= 0 AND flat_rate <= 1)",
            name="ck_state_tax_configs_valid_rate",
        ),
        # F-077 / C-24: ``standard_deduction`` is nullable (NULL =
        # state has no standard deduction); when present, must be
        # non-negative.
        db.CheckConstraint(
            "standard_deduction IS NULL OR standard_deduction >= 0",
            name="ck_state_tax_configs_nonneg_standard_deduction",
        ),
        # F-077 / C-24: tax_year sweep paired with the tax_bracket_sets
        # equivalent.
        db.CheckConstraint(
            "tax_year >= 2000 AND tax_year <= 2100",
            name="ck_state_tax_configs_valid_tax_year",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # F-073 / C-43: explicit ondelete=RESTRICT + fk_* name on the
    # tax-type ref-table FK.  See app/extensions.py for the full
    # SHEKEL_NAMING_CONVENTION rationale.
    tax_type_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.tax_types.id",
            name="fk_state_tax_configs_tax_type_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # T-P5: the filing-status dimension.  RESTRICT + fk_* name mirror the
    # tax_bracket_sets filing-status FK; a deleted ref row must not orphan
    # a config.
    filing_status_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.filing_statuses.id",
            name="fk_state_tax_configs_filing_status_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    state_code = db.Column(db.String(2), nullable=False)
    tax_year = db.Column(db.Integer, nullable=False)
    flat_rate = db.Column(db.Numeric(5, 4))
    standard_deduction = db.Column(db.Numeric(12, 2))

    # Relationships
    tax_type = db.relationship("TaxType", lazy="joined")
    filing_status = db.relationship("FilingStatus", lazy="joined")

    def __repr__(self):
        return (
            f"<StateTaxConfig {self.state_code} {self.tax_year} "
            f"status_id={self.filing_status_id} rate={self.flat_rate}>"
        )


class FicaConfig(UserScopedMixin, CreatedAtMixin, db.Model):
    """FICA (Social Security + Medicare) tax configuration per year."""

    __tablename__ = "fica_configs"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "tax_year",
            name="uq_fica_configs_user_year",
        ),
        db.CheckConstraint("ss_rate >= 0 AND ss_rate <= 1", name="ck_fica_configs_valid_ss_rate"),
        db.CheckConstraint("ss_wage_base > 0", name="ck_fica_configs_positive_wage_base"),
        db.CheckConstraint(
            "medicare_rate >= 0 AND medicare_rate <= 1",
            name="ck_fica_configs_valid_medicare_rate",
        ),
        db.CheckConstraint(
            "medicare_surtax_rate >= 0 AND medicare_surtax_rate <= 1",
            name="ck_fica_configs_valid_surtax_rate",
        ),
        db.CheckConstraint(
            "medicare_surtax_threshold > 0",
            name="ck_fica_configs_positive_surtax_threshold",
        ),
        # F-077 / C-24: tax_year sweep paired with the tax_bracket_sets
        # and state_tax_configs equivalents.
        db.CheckConstraint(
            "tax_year >= 2000 AND tax_year <= 2100",
            name="ck_fica_configs_valid_tax_year",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    tax_year = db.Column(db.Integer, nullable=False)
    # E-11 / E-28: every Python-side ``default`` on these
    # ``Numeric`` money/rate columns is a ``Decimal`` constructed
    # from a string, matching the C24-5 fix on
    # ``InvestmentParams.assumed_annual_return`` and the
    # ``DEFAULT_FICA`` seed (auth_service.py).  Pre-fix these were
    # ``float``/``int`` literals (``0.0620`` etc.); PostgreSQL
    # re-quantises on store so the persisted value was unaffected,
    # but ORM code paths that read ``Column.default.arg`` saw a
    # float and propagated the imprecision.  The ``server_default``
    # is the storage-tier counterpart (a SQL string).
    ss_rate = db.Column(
        db.Numeric(5, 4), nullable=False, default=Decimal("0.0620"),
        server_default=db.text("0.0620"),
    )
    ss_wage_base = db.Column(
        db.Numeric(12, 2), nullable=False, default=Decimal("176100"),
        server_default=db.text("176100"),
    )
    medicare_rate = db.Column(
        db.Numeric(5, 4), nullable=False, default=Decimal("0.0145"),
        server_default=db.text("0.0145"),
    )
    medicare_surtax_rate = db.Column(
        db.Numeric(5, 4), nullable=False, default=Decimal("0.0090"),
        server_default=db.text("0.0090"),
    )
    medicare_surtax_threshold = db.Column(
        db.Numeric(12, 2), nullable=False, default=Decimal("200000"),
        server_default=db.text("200000"),
    )

    def __repr__(self):
        return f"<FicaConfig year={self.tax_year}>"


class StateChildDeduction(CreatedAtMixin, db.Model):
    """State per-child deduction tier (AGI-tiered, per filing status).

    Taxes slice T-P5 (finding 2): the NC child deduction (N.C.G.S.
    105-153.5(a1)) is a per-qualifying-child deduction whose amount depends
    on BOTH filing status AND an adjusted-gross-income tier.  Each row is one
    tier: the per-child deduction that applies when AGI falls in
    ``(agi_min, agi_max]`` for a given ``(state, year, filing_status)``.

    Boundary semantics follow the NC statute/D-401 table wording exactly:
    a tier reads "Up to $X" (inclusive of X) then "Over $X - Up to $Y", so a
    threshold dollar value belongs to the LOWER / more-generous tier.  The
    tier lookup therefore selects the row with the smallest ``agi_max`` that
    is >= AGI; the open-ended top tier (``agi_max IS NULL``, deduction $0)
    catches everything above the last finite bound.  ``agi_min`` is the
    exclusive lower bound ("Over $X"; 0 for the first tier) -- stored for the
    ordering CHECK and the uniqueness key; the lookup keys on ``agi_max``.
    """

    __tablename__ = "state_child_deductions"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "state_code", "tax_year", "filing_status_id", "agi_min",
            name="uq_state_child_deductions_user_state_year_status_agi",
        ),
        db.CheckConstraint(
            "agi_min >= 0", name="ck_state_child_deductions_nonneg_agi_min",
        ),
        db.CheckConstraint(
            "agi_max IS NULL OR agi_max > agi_min",
            name="ck_state_child_deductions_agi_order",
        ),
        db.CheckConstraint(
            "deduction_per_child >= 0",
            name="ck_state_child_deductions_nonneg_deduction",
        ),
        db.CheckConstraint(
            "tax_year >= 2000 AND tax_year <= 2100",
            name="ck_state_child_deductions_valid_tax_year",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # RESTRICT + fk_* name mirror the sibling filing-status FKs.
    filing_status_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.filing_statuses.id",
            name="fk_state_child_deductions_filing_status_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    state_code = db.Column(db.String(2), nullable=False)
    tax_year = db.Column(db.Integer, nullable=False)
    # Exclusive lower AGI bound of the tier (0 for the first tier).
    agi_min = db.Column(db.Numeric(12, 2), nullable=False)
    # Inclusive upper AGI bound; NULL = open-ended top tier.  Nullable is
    # deliberate: the top tier ("Over $X", deduction $0) has no upper bound.
    agi_max = db.Column(db.Numeric(12, 2))
    deduction_per_child = db.Column(db.Numeric(12, 2), nullable=False)
    # Declared here rather than taken from UserScopedMixin, whose key takes
    # PostgreSQL's default name: migration b7f3a2c1d4e5 named this one, and
    # production holds that name (plan#1858).  Last of the class's own
    # columns, where the mixin's rendered, so the models emit the table's
    # columns in the order they did.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "auth.users.id",
            name="fk_state_child_deductions_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    # Relationships
    filing_status = db.relationship("FilingStatus", lazy="joined")

    def __repr__(self):
        return (
            f"<StateChildDeduction {self.state_code} {self.tax_year} "
            f"status_id={self.filing_status_id} "
            f"({self.agi_min}-{self.agi_max}) per_child={self.deduction_per_child}>"
        )
