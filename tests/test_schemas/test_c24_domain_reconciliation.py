"""
HIGH-06 / E-28 -- Commit 24 domain-reconciliation tests.

The financial-calculations audit (``docs/audits/financial_calculations/
08_findings.md`` HIGH-06) names five concrete defects on rate /
threshold columns whose Marshmallow domain disagreed with the DB
``CHECK`` or behaved wrongly on blank / zero:

  PA-01 ``user_settings.trend_alert_threshold`` -- Marshmallow
    ``Integer Range(1, 100)`` vs DB ``CHECK(0..1)``.  Only the
    literal value 1 nominally satisfied both, and the route's
    silent ``/100`` reconciled it in practice; zero was rejected
    although E-12 says "zero is a value, not missing."
    (Column, field, and its C24-1 pins removed 2026-07-10 with the
    retired spending trend engine; PA-01 stays here as the audit's
    historical record.)

  PA-02 ``apy``, ``interest_rate`` (loan / rate_history),
    ``inflation_rate`` (escrow), ``default_inflation_rate`` --
    Marshmallow ``Range(0, 100)`` (percent) vs DB ``CHECK(0..1)``
    (fraction).  A schema-accepted ``50.0`` was 0.50 in storage;
    a future writer that forgot the route's /100 would commit
    a CHECK violation as a 500.

  Q-24 #2 ``interest_params.apy`` first-save silent default --
    ``server_default="0.04500"`` materialised a 4.5% rate on any
    INSERT that omitted ``apy``.  Two auto-create sites in
    ``app/routes/accounts.py`` emitted no ``apy``, so a first-
    save HYSA / HSA projected ghost interest the user never
    configured.

  Q-24 #3 ``investment_params.annual_contribution_limit`` --
    one stored ``Decimal("0")`` produced three contradictory
    behaviours via three truthiness reads, while the engine's
    ``is not None`` read treated it as a hard cap of zero.

  E-11 / E-28 ``investment_params.assumed_annual_return`` --
    Python ``default=0.07000`` was a float literal.

Each defect lands a pinned test below.  The plan calls these
C24-1 through C24-6 (Section 9 / Commit 24 / subsection E); the
file is named for the commit and the IDs are preserved as
``test_c24_<n>_*`` so a future audit cross-walk is trivial.
"""

from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app import ref_cache
from app.enums import CompoundingFrequencyEnum
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.tax_config import FicaConfig
from app.schemas.validation import (
    EscrowComponentSchema,
    InterestParamsCreateSchema,
    InterestParamsUpdateSchema,
    InvestmentParamsCreateSchema,
    LoanParamsCreateSchema,
    LoanParamsUpdateSchema,
    RateChangeSchema,
    RefinanceSchema,
    UserSettingsSchema,
)


# ── C24-2 rate field domain matches CHECK ────────────────────────


class TestC24_2RateFieldDomainMatchesCheck:
    """C24-2: every reconciled rate schema accepts / rejects the same
    boundary set as its DB CHECK ``[0, 1]``.

    The schema's ``@pre_load`` normalizes percent input to fraction
    so 100 -> 1.0 (boundary inclusive), 101 -> 1.01 (rejected
    matching the DB), and -0.01 stays negative (rejected matching
    the DB).
    """

    @pytest.mark.parametrize(
        "schema_factory, field, extra",
        [
            # InterestParamsCreateSchema is covered by its own test below
            # (#38): its required ``compounding_frequency_id`` is a ref id
            # that can only be resolved at runtime, not in this
            # collection-time parametrize table.
            (InterestParamsUpdateSchema, "apy", {}),
            (
                LoanParamsCreateSchema,
                "interest_rate",
                {
                    "original_principal": "250000.00",
                    "anchor_balance": "200000.00",
                    "anchor_date": "2020-01-01",
                    "term_months": "360",
                    "origination_date": "2020-01-01",
                    "payment_day": "1",
                },
            ),
            (LoanParamsUpdateSchema, "interest_rate", {}),
            (
                RateChangeSchema,
                "interest_rate",
                {"effective_date": "2026-04-01"},
            ),
            (
                RefinanceSchema,
                "new_rate",
                {"new_term_months": "360"},
            ),
            (EscrowComponentSchema, "inflation_rate", {
                "name": "Property Tax",
                "annual_amount": "4800.00",
            }),
        ],
    )
    def test_boundary_zero_accepted_one_accepted_above_rejected(
        self, schema_factory, field, extra,
    ):
        """Each rate field accepts 0% and 100% and rejects 101%.

        Hand-check:
        - 0 / 100 = 0 -> Range(0, 1) accepts.
        - 100 / 100 = 1.0 -> Range(0, 1) accepts (inclusive upper).
        - 101 / 100 = 1.01 -> Range(0, 1) rejects.
        """
        schema = schema_factory()

        loaded = schema.load({field: "0", **extra})
        assert loaded[field] == Decimal("0")

        loaded = schema.load({field: "100", **extra})
        assert loaded[field] == Decimal("1.0")

        with pytest.raises(ValidationError) as exc:
            schema.load({field: "101", **extra})
        assert field in exc.value.messages

    def test_interest_create_apy_boundary(self):
        """InterestParamsCreateSchema accepts apy 0% / 100%, rejects 101%.

        Split out of the table above because the schema's required
        ``compounding_frequency_id`` is a ref id resolved from ref_cache
        at runtime (#38), which a collection-time parametrize cannot
        provide.  Same 0/100/101 boundary contract as the other rate
        fields.
        """
        schema = InterestParamsCreateSchema()
        freq_id = ref_cache.compounding_frequency_id(
            CompoundingFrequencyEnum.DAILY,
        )

        loaded = schema.load({"apy": "0", "compounding_frequency_id": freq_id})
        assert loaded["apy"] == Decimal("0")

        loaded = schema.load(
            {"apy": "100", "compounding_frequency_id": freq_id}
        )
        assert loaded["apy"] == Decimal("1.0")

        with pytest.raises(ValidationError) as exc:
            schema.load({"apy": "101", "compounding_frequency_id": freq_id})
        assert "apy" in exc.value.messages

    def test_loan_negative_rejected(self):
        """Below 0% rejected on loan create.

        Hand-check: -0.01 / 100 = -0.0001 -> Range(0, 1) rejects.
        """
        with pytest.raises(ValidationError) as exc:
            LoanParamsCreateSchema().load({
                "original_principal": "250000.00",
                "anchor_balance": "200000.00",
                "anchor_date": "2020-01-01",
                "interest_rate": "-0.01",
                "term_months": "360",
                "origination_date": "2020-01-01",
                "payment_day": "1",
            })
        assert "interest_rate" in exc.value.messages


# ── C24-3 apy first-save explicit; no silent default ─────────────


class TestC24_3ApyFirstSaveExplicit:
    """C24-3: ``InterestParams.apy`` no longer carries a silent
    ``server_default``; an INSERT that omits ``apy`` fails the
    storage-tier NOT NULL constraint rather than materialising a
    ghost 4.5%.

    The fix is twofold (Commit 24): the model drops the
    ``server_default``, and the application-tier auto-create sites
    in ``app/routes/accounts.py`` write ``apy=Decimal("0")``
    explicitly (E-12: zero is the "no interest configured"
    sentinel).  These tests pin the storage-tier removal; the
    auto-create behaviour is pinned in
    ``tests/test_routes/test_hysa.py::TestCreateHysaAccount`` and
    in ``tests/test_routes/test_accounts/test_creation_redirects.py``'s
    account-creation suite.
    """

    def test_apy_column_has_no_server_default(self):
        """The model column carries no ``server_default``.

        Pre-fix this was ``server_default=DefaultClause("0.04500")``.
        Post-fix it is ``None``.  Using the SQLAlchemy ORM
        introspection so the assertion survives string-formatting
        renames of the migration file.
        """
        apy_col = InterestParams.__table__.c.apy
        assert apy_col.server_default is None

    def test_apy_column_remains_not_null(self):
        """The column stays NOT NULL so the lack of a default
        surfaces as a clean storage-tier error on a first save
        that omits ``apy``."""
        apy_col = InterestParams.__table__.c.apy
        assert apy_col.nullable is False


# ── C24-4 annual_contribution_limit one meaning ──────────────────


class TestC24_4ContributionLimitZeroOneMeaning:
    """C24-4: a stored ``annual_contribution_limit == Decimal("0")``
    has ONE consistent meaning across all consumers: "hard cap of
    $0; no contributions allowed."  Pre-fix three dashboard read
    sites used Python truthiness (treating ``0`` as "no limit
    configured", a falsehood under E-12) while the growth engine
    used ``is not None`` (the correct semantics).

    The chosen meaning is documented in the
    :class:`InvestmentParams` docstring; these tests pin the
    behaviour for one representative consumer per surface so a
    future regression to truthiness lights up a named test.
    """

    def test_schema_accepts_zero_limit(self):
        """The schema's ``Range(min=0, ...)`` admits the explicit
        zero.  Hand-check: 0 is inclusive lower bound."""
        loaded = InvestmentParamsCreateSchema().load({
            "assumed_annual_return": "0.07",
            "annual_contribution_limit": "0",
        })
        assert loaded["annual_contribution_limit"] == Decimal("0.00")

    def test_growth_engine_caps_at_zero(self):
        """``growth_engine.project_balance`` reads ``is not None``,
        so a zero cap produces a zero contribution every period.

        Hand-check: ``min(period_contribution, max(0 - ytd, 0))``
        with ``ytd=0`` evaluates to ``min(x, 0) = 0``, so every
        period's contribution is zero regardless of the configured
        ``periodic_contribution``.
        """
        from app.services import growth_engine  # pylint: disable=import-outside-toplevel
        from datetime import date  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import biweekly_window  # pylint: disable=import-outside-toplevel

        periods = biweekly_window(date(2026, 1, 1), 7)
        # A non-empty axis, so the assertion below exercises the cap branch.
        assert periods

        result = growth_engine.project_balance(
            current_balance=Decimal("10000.00"),
            assumed_annual_return=Decimal("0.07"),
            periods=periods,
            periodic_contribution=Decimal("500.00"),
            employer_params={"type": "none"},
            annual_contribution_limit=Decimal("0"),
            ytd_contributions_start=Decimal("0"),
        )
        for pb in result:
            assert pb.contribution == Decimal("0"), (
                "Zero limit must cap every period's contribution at $0; "
                f"got {pb.contribution} for period {pb.period_id}"
            )


# ── C24-5 assumed_return default is Decimal string, not float ───


class TestC24_5AssumedReturnDefault:
    """C24-5: ``InvestmentParams.assumed_annual_return`` Python-side
    ``default`` is a ``Decimal`` constructed from a string, not a
    ``float`` literal.  E-11 / E-28 coding-standards facet: a
    ``float`` literal introduces imprecision into any read of
    ``Column.default.arg`` (the persisted value is unaffected
    because PostgreSQL re-quantises on store).
    """

    def test_default_is_decimal_not_float(self):
        """``Column.default.arg`` is a ``Decimal`` instance.

        Hand-check: ``isinstance(Decimal("0.07000"), Decimal) is True``,
        ``isinstance(0.07000, Decimal) is False``.
        """
        col = InvestmentParams.__table__.c.assumed_annual_return
        # SQLAlchemy wraps the Python default in a ``ColumnDefault``
        # object; ``.arg`` is the raw value passed to ``default=``.
        default_arg = col.default.arg
        assert isinstance(default_arg, Decimal), (
            f"Expected Decimal default, got {type(default_arg).__name__}"
        )
        assert default_arg == Decimal("0.07000")


# ── DH-#45 FicaConfig defaults are Decimal strings, not float ────


class TestFicaConfigDecimalDefaults:
    """DH-#45 / E-11 / E-28: every Python-side ``default`` on
    ``FicaConfig``'s ``Numeric`` money/rate columns is a ``Decimal``
    constructed from a string, not a ``float``/``int`` literal.

    This is the same defect class C24-5 fixed on
    ``InvestmentParams.assumed_annual_return`` but left unfixed on the
    FICA rate/base columns (``ss_rate``/``medicare_rate``/
    ``medicare_surtax_rate`` were ``float`` literals such as
    ``0.0620``; ``ss_wage_base``/``medicare_surtax_threshold`` were
    ``int`` literals).  A ``float`` literal introduces imprecision into
    any read of ``Column.default.arg`` (the persisted value is
    unaffected because PostgreSQL re-quantises on store).  The
    storage-tier ``server_default`` side is covered separately by the
    C-25 raw-INSERT test
    (``test_fica_configs_rate_defaults_fire_on_raw_insert``).
    """

    # (column name, expected Decimal default) -- the values match the
    # ``DEFAULT_FICA`` seed in ``auth_service.py``.
    _EXPECTED = [
        ("ss_rate", Decimal("0.0620")),
        ("ss_wage_base", Decimal("176100")),
        ("medicare_rate", Decimal("0.0145")),
        ("medicare_surtax_rate", Decimal("0.0090")),
        ("medicare_surtax_threshold", Decimal("200000")),
    ]

    @pytest.mark.parametrize("column_name, expected", _EXPECTED)
    def test_default_is_decimal_not_float(self, column_name, expected):
        """``Column.default.arg`` is a ``Decimal`` of the expected value.

        Hand-check: ``isinstance(Decimal("0.0620"), Decimal) is True``,
        ``isinstance(0.0620, Decimal) is False``.
        """
        col = FicaConfig.__table__.c[column_name]
        # SQLAlchemy wraps the Python default in a ``ColumnDefault``
        # object; ``.arg`` is the raw value passed to ``default=``.
        default_arg = col.default.arg
        assert isinstance(default_arg, Decimal), (
            f"{column_name}: expected Decimal default, "
            f"got {type(default_arg).__name__}"
        )
        assert default_arg == expected


# ── C24-6 migration upgrade/downgrade round-trips ────────────────


class TestC24_6MigrationRoundTrip:
    """C24-6: the Commit-24 migration is reversible.

    The migration's only DDL is dropping the
    ``server_default="0.04500"`` on ``budget.interest_params.apy``;
    downgrade restores it byte-identically.  A round-trip
    (upgrade -> downgrade -> upgrade) is exercised in the
    development environment as part of the Definition of Done; the
    test below asserts the migration module is importable and
    declares both ``upgrade`` and ``downgrade`` so a future
    refactor that breaks the chain is caught at unit-test time.
    """

    def test_migration_module_has_upgrade_and_downgrade(self):
        """The migration declares both directions, and the
        downgrade is not a bare ``pass``."""
        import importlib  # pylint: disable=import-outside-toplevel
        import inspect  # pylint: disable=import-outside-toplevel

        module = importlib.import_module(
            "migrations.versions."
            "c24a1f6e0b8d_reconcile_check_domains_interest_apy_",
        )
        assert callable(module.upgrade)
        assert callable(module.downgrade)
        downgrade_source = inspect.getsource(module.downgrade)
        # A bare ``pass`` would let ``flask db downgrade`` chain
        # past this revision while leaving the server_default
        # missing -- the coding standard requires either real
        # reversal SQL or an explicit ``NotImplementedError``.
        assert "server_default" in downgrade_source, (
            "Downgrade must explicitly restore the server_default; "
            "got a body that does not reference server_default."
        )
