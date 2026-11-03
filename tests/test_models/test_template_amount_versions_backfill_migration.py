"""
Shekel Budget App -- ``a9d3c15e7f42`` amount-series backfill tests (X-au-a)

The migration creates ``budget.template_amount_versions`` and reconstructs each
recurring definition's price history from the rows it already generated.  Which
rows are trustworthy EVIDENCE is not a judgement call -- it falls out of what
regeneration does to each one
(``app/services/_recurrence_common.py::classify_maintain_work``):

  * an **overridden or soft-deleted** row is a CONFLICT the sweep leaves alone,
    so its amount is a per-row figure the user typed or a price that has gone
    stale -- **not evidence**;
  * an **immutable (settled)** row is skipped by the sweep, so its amount is
    frozen at the price in effect when it was generated -- **evidence**, and
    where the real history lives;
  * every other row is deleted and recreated at the template's current
    ``default_amount`` -- **evidence**, consistently the newest price.

Each test below engineers exactly one of those shapes and invokes the
migration's own module-level ``backfill_template_amount_versions`` (the
``test_ledger_account_backfill`` / ``test_loan_anchor_backfill`` pattern), so it
grades the shipped derivation rather than a re-statement of it.  The migration is
already at HEAD when these run -- the template builder upgraded base->head against
an empty ``budget.transactions``, so the in-chain backfill was a no-op -- which is
what leaves the table empty for each test to fill.

The end-to-end proof is separate and was run against a **production clone**: 44
eligible templates yielded 47 versions, and the resolver reproduced the stored
amount of all **625** minable rows with **0** mismatches (see the step's commit
message).
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.ref import TransactionType
from app.models.scenario import Scenario
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from tests._test_helpers import (
    create_savings_account,
    generate_row_of,
    generate_transfer_of,
    load_migration_module,
    make_every_period_rule,
    make_salary_profile,
)
from app.models.amount_ownership import AmountOwnership


_MIGRATION = load_migration_module(
    "a9d3c15e7f42_template_amount_versions.py"
)


# ── Helpers ──────────────────────────────────────────────────────────


def _run_backfill():
    """Invoke the migration's own backfill against the test session."""
    _MIGRATION.backfill_template_amount_versions(_db.session)
    _db.session.flush()


def _versions(template, column="transaction_template_id"):
    """Return ``(effective_date, amount)`` for a template's versions, ascending."""
    rows = _db.session.execute(_db.text(
        "SELECT effective_date, amount FROM budget.template_amount_versions "
        f"WHERE {column} = :t ORDER BY effective_date"
    ), {"t": template.id}).fetchall()
    return [(row.effective_date, row.amount) for row in rows]

def _template(seed_user, name="Geico", amount="165.30"):
    """Create a plain recurring-expense template (no series yet).

    It carries the every-paycheck cadence the engine needs to write its rows
    (:func:`_row`) and NO price series: the series is what the backfill under
    test must find EMPTY and then build, so the shared priced builders are
    the wrong fixture here.
    """
    expense = _db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name=name,
    )
    _db.session.add(template)
    _db.session.flush()
    make_every_period_rule(_db.session, template)
    return template


def _row(  # pylint: disable=too-many-arguments
    template, period, amount, due_date, *,
    status=StatusEnum.PROJECTED, is_override=False, is_deleted=False,
    scenario=None,
):
    """Create one generated transaction for *template* with a stated shape.

    The row is the ENGINE's (:func:`generate_row_of`, plan step balance:X-cf),
    and the shape this migration MINED is then laid back onto it: the row
    holds its own copy of the price (the pre-X-au-e stored figure the backfill
    reads), the due date the case names, and the status, flags and scenario
    that decide whether it is evidence.  The engine writes into the baseline;
    a what-if row is moved after the fact, which is the one axis that case is
    about.
    """
    txn = generate_row_of(template, period)
    txn.amount_ownership = AmountOwnership.own(Decimal(amount))
    txn.due_date = due_date
    txn.status_id = ref_cache.status_id(status)
    txn.is_override = is_override
    txn.is_deleted = is_deleted
    if scenario is not None:
        txn.scenario_id = scenario.id
    _db.session.flush()
    return txn


def _transfer_template(seed_user, savings, name, amount):
    """Create a plain recurring transfer template (no series yet).

    :func:`_template`'s twin: the every-paycheck cadence the transfer engine
    needs (:func:`_transfer_row`) and NO price series, for the same reason.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        name=name,
    )
    _db.session.add(template)
    _db.session.flush()
    make_every_period_rule(_db.session, template)
    return template


def _transfer_row(template, period, amount, due_date, *, is_override=False):
    """Create one generated transfer for *template* with a stated shape.

    :func:`_row` one table over (plan step balance:X-ch): the transfer is the
    ENGINE's (:func:`generate_transfer_of`), and the shape this migration
    MINED is laid back onto the PARENT -- its own copy of the price (the
    pre-X-au-f stored figure the transfer arm reads), the due date the case
    names, and the override flag.  The two legs are left as the engine wrote
    them; the backfill's transfer arm reads ``budget.transfers`` alone.
    """
    xfer = generate_transfer_of(template, period)
    xfer.amount_ownership = AmountOwnership.own(Decimal(amount))
    xfer.due_date = due_date
    xfer.is_override = is_override
    _db.session.flush()
    return xfer


# Stamp a timestamptz at MIDDAY in the display timezone, so the civil day the
# backfill reads is the day the test named on any date and under either DST
# offset.  Midnight would not do: the backfill converts to the user's zone
# before taking the date (a stated day is the OWNER's civil day, not the
# session's UTC one), and midnight UTC is the PREVIOUS day in New York.
_MIDDAY_DISPLAY = (
    "(CAST(:{param} AS date) + time '12:00') AT TIME ZONE 'America/New_York'"
)


def _stamp(template, table, *, created=None, updated=None):
    """Force a template's ``created_at`` / ``updated_at`` civil day.

    Both carry ``now()`` server defaults, and the backfill's no-evidence and
    disagreement arms READ them, so a test that engineers those arms has to
    pin them rather than assert against whenever the row happened to be
    inserted.
    """
    sets, params = [], {"t": template.id}
    if created is not None:
        sets.append(f"created_at = {_MIDDAY_DISPLAY.format(param='c')}")
        params["c"] = created
    if updated is not None:
        sets.append(f"updated_at = {_MIDDAY_DISPLAY.format(param='u')}")
        params["u"] = updated
    _db.session.execute(_db.text(
        f"UPDATE budget.{table} SET {', '.join(sets)} WHERE id = :t"
    ), params)
    _db.session.flush()


# ── The evidence rule ────────────────────────────────────────────────


class TestMinedHistory:
    """Which rows the backfill reads, and which it must not."""

    def test_settled_rows_carry_the_price_history(
        self, app, db, seed_user, seed_periods,
    ):
        """Production's ``Geico`` shape: three prices, three versions.

        Two settled bills at $178.00, one at $178.32, then the projected tail at
        today's $165.30 -- so the series is $178.00 from the first bill's due
        date, $178.32 from the third's, and $165.30 from the fourth's.  Each
        version is dated by the DUE date of the first row carrying that price,
        never by the paycheck that funds it.
        """
        with app.app_context():
            template = _template(seed_user)
            prices = [
                ("178.00", date(2026, 4, 1), StatusEnum.DONE),
                ("178.00", date(2026, 5, 1), StatusEnum.DONE),
                ("178.32", date(2026, 6, 1), StatusEnum.DONE),
                ("165.30", date(2026, 9, 1), StatusEnum.PROJECTED),
                ("165.30", date(2026, 10, 1), StatusEnum.PROJECTED),
            ]
            for (amount, due, status), period in zip(prices, seed_periods):
                _row(template, period, amount, due, status=status)

            _run_backfill()

            assert _versions(template) == [
                (date(2026, 4, 1), Decimal("178.00")),
                (date(2026, 6, 1), Decimal("178.32")),
                (date(2026, 9, 1), Decimal("165.30")),
            ]

    def test_an_overridden_row_is_not_evidence(
        self, app, db, seed_user, seed_periods,
    ):
        """A hand-typed per-row figure is the ROW's, not the definition's price.

        Production's ``Electricity`` shape: the stored budget stayed $300.00
        while individual months were corrected to $222.22 and $370.00.  Mining
        those would invent two price changes that never happened.
        """
        with app.app_context():
            template = _template(seed_user, name="Electricity", amount="300.00")
            _row(template, seed_periods[0], "300.00",
                 date(2026, 4, 14), status=StatusEnum.DONE)
            _row(template, seed_periods[1], "222.22",
                 date(2026, 5, 14), status=StatusEnum.DONE, is_override=True)
            _row(template, seed_periods[2], "370.00",
                 date(2026, 6, 14), status=StatusEnum.DONE, is_override=True)
            _row(template, seed_periods[3], "300.00",
                 date(2026, 7, 14))

            _run_backfill()

            assert _versions(template) == [
                (date(2026, 4, 14), Decimal("300.00")),
            ]

    def test_a_soft_deleted_row_is_not_evidence(
        self, app, db, seed_user, seed_periods,
    ):
        """The sweep leaves a removed row alone, so its amount can be stale.

        Reading it would let a price the definition has already left place a
        version -- the same hazard an override carries, by the same mechanism.
        """
        with app.app_context():
            template = _template(seed_user, amount="165.30")
            _row(template, seed_periods[0], "999.99",
                 date(2026, 4, 1), is_deleted=True)
            _row(template, seed_periods[1], "165.30",
                 date(2026, 5, 1))

            _run_backfill()

            assert _versions(template) == [
                (date(2026, 5, 1), Decimal("165.30")),
            ]

    def test_a_non_baseline_scenario_is_not_evidence(
        self, app, db, seed_user, seed_periods,
    ):
        """Regeneration is baseline-scoped, so a what-if row can hold a stale price.

        Interleaving two scenarios' rows would also manufacture a run boundary
        out of the ordering rather than out of a price change.
        """
        with app.app_context():
            what_if = Scenario(
                user_id=seed_user["user"].id, name="What if", is_baseline=False,
            )
            db.session.add(what_if)
            db.session.flush()

            template = _template(seed_user, amount="165.30")
            _row(template, seed_periods[0], "500.00",
                 date(2026, 4, 1), scenario=what_if)
            _row(template, seed_periods[1], "165.30",
                 date(2026, 5, 1))

            _run_backfill()

            assert _versions(template) == [
                (date(2026, 5, 1), Decimal("165.30")),
            ]


# ── The eligibility rule ─────────────────────────────────────────────


class TestEligibility:
    """A derived amount gets no series, however many rows record it."""

    def test_a_salary_linked_template_gets_no_series(
        self, app, db, seed_user, seed_periods,
    ):
        """Its rows are paycheck-calculated, so their spread is not a price history.

        On production ``Data Manager`` is the only excluded template -- and the
        only one whose newest row disagrees with its stored amount, which is the
        exclusion demonstrating itself.
        """
        with app.app_context():
            template = _template(seed_user, name="Data Manager", amount="2473.38")
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.flush()
            _row(template, seed_periods[0], "2473.38",
                 date(2026, 4, 1), status=StatusEnum.RECEIVED)
            _row(template, seed_periods[1], "2562.67",
                 date(2026, 5, 1))

            _run_backfill()

            assert _versions(template) == []

    def test_a_derive_mode_loan_payment_gets_no_series(
        self, app, db, seed_user,
    ):
        """Its ``default_amount`` is a P&I + escrow snapshot, not a stated price."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Mortgage",
            )
            template.settings = LoanPaymentSettings(
                derive_from_loan=True, extra_principal=Decimal("0.00"),
            )
            db.session.add(template)
            db.session.flush()

            _run_backfill()

            assert _versions(template, "transfer_template_id") == []

    def test_a_manual_loan_payment_gets_one(self, app, db, seed_user):
        """The operator owns the base cash in manual mode, so it IS stated.

        Both of production's loan payments are manual (``loan_payment_settings``
        is empty there), so both get a series.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Van Payment",
            )
            template.settings = LoanPaymentSettings(
                derive_from_loan=False, extra_principal=Decimal("0.00"),
            )
            db.session.add(template)
            db.session.flush()
            _stamp(template, "transfer_templates",
                   created=date(2026, 4, 22), updated=date(2026, 4, 22))

            _run_backfill()

            assert _versions(template, "transfer_template_id") == [
                (date(2026, 4, 22), Decimal("531.94")),
            ]


# ── The scalar's tail ────────────────────────────────────────────────


class TestScalarTail:
    """``default_amount`` is authoritative today, so every series ends at it."""

    def test_a_template_with_no_evidence_is_seeded_at_creation(
        self, app, db, seed_user, seed_periods,
    ):
        """Production's ``Rogue Equipment``: one row, hand-edited, so no evidence.

        Its price is still known -- it is the stored ``default_amount`` -- and
        the only date the database holds for when it was stated is the day the
        template row was created.
        """
        with app.app_context():
            template = _template(seed_user, name="Rogue Equipment", amount="2000.00")
            _stamp(template, "transaction_templates", created=date(2026, 7, 23))
            _row(template, seed_periods[0], "2000.00",
                 date(2026, 7, 30), is_override=True)

            _run_backfill()

            assert _versions(template) == [
                (date(2026, 7, 23), Decimal("2000.00")),
            ]

    def test_a_scalar_the_rows_never_recorded_is_appended(
        self, app, db, seed_user, seed_periods,
    ):
        """An amount edited but never regenerated into a row still ends the series.

        Otherwise the series' newest version would contradict the column that is
        still authoritative.  The date is the template's ``updated_at`` -- the
        only record of when the figure was set.
        """
        with app.app_context():
            template = _template(seed_user, amount="180.00")
            _row(template, seed_periods[0], "165.30",
                 date(2026, 4, 1), status=StatusEnum.DONE)
            _stamp(template, "transaction_templates", updated=date(2026, 6, 15))

            _run_backfill()

            assert _versions(template) == [
                (date(2026, 4, 1), Decimal("165.30")),
                (date(2026, 6, 15), Decimal("180.00")),
            ]

    def test_the_scalar_lands_after_the_LAST_row_of_the_final_run(
        self, app, db, seed_user, seed_periods,
    ):
        """The ordering guard reads the run's END, not its start.

        Adversarial review found it reading the start: rows at $100.00 due Jan,
        Feb and Mar with the scalar stated on Feb 15 put the scalar INSIDE the
        run, and the March row -- which stores $100.00 -- then resolved to
        $120.00.  ``updated_at`` on or before the LAST minable row is
        unorderable, so this shape is now refused rather than mispriced.
        """
        with app.app_context():
            template = _template(seed_user, name="Mid-run", amount="120.00")
            for amount, due, period in zip(
                ("100.00", "100.00", "100.00"),
                (date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)),
                seed_periods,
            ):
                _row(template, period, amount, due,
                     status=StatusEnum.DONE)
            _stamp(template, "transaction_templates", updated=date(2026, 2, 15))

            with pytest.raises(RuntimeError, match="cannot be derived"):
                _run_backfill()
            db.session.rollback()

    def test_an_unorderable_scalar_aborts_rather_than_inventing_a_date(
        self, app, db, seed_user, seed_periods,
    ):
        """When the evidence and the scalar cannot be ordered, a human decides.

        ``updated_at`` on or before the newest evidence means the migration
        cannot say when the current figure took effect; picking one would put a
        date in the ledger that nothing supports, so it fails loud with both
        figures instead.
        """
        with app.app_context():
            template = _template(seed_user, name="Muddle", amount="180.00")
            _row(template, seed_periods[0], "165.30",
                 date(2027, 4, 1), status=StatusEnum.DONE)
            _stamp(template, "transaction_templates", updated=date(2026, 6, 15))

            with pytest.raises(RuntimeError, match="cannot be derived"):
                _run_backfill()
            db.session.rollback()


class TestContradictoryEvidence:
    """One date cannot hold two prices, so the backfill refuses to guess."""

    def test_two_amounts_on_one_date_abort_rather_than_collide(
        self, app, db, seed_user, seed_periods,
    ):
        """Each row opens a run, both runs date at that day, and a day has one price.

        Reachable without ever touching an amount: a hand-edited ``due_date``
        moves the encoding's sort key and does NOT set ``is_override`` (the
        generic field loop applies the column while the flag fires only for an
        amount or period change).  Before this guard the shipped backfill died
        on the partial unique index with an ``IntegrityError`` mid-upgrade.
        """
        with app.app_context():
            template = _template(seed_user, name="Collision", amount="150.00")
            _row(template, seed_periods[0], "100.00",
                 date(2026, 1, 1))
            _row(template, seed_periods[1], "150.00",
                 date(2026, 1, 1))

            with pytest.raises(RuntimeError, match="two different amounts"):
                _run_backfill()
            db.session.rollback()

    def test_two_rows_on_one_date_at_ONE_amount_are_fine(
        self, app, db, seed_user, seed_periods,
    ):
        """The guard is about contradiction, not about a repeated date.

        A paycheck cadence of 30 days or more legitimately lands one definition
        on a date twice; while both rows agree they are one run and say one
        thing, so refusing them would refuse ordinary data.
        """
        with app.app_context():
            template = _template(seed_user, name="Twice", amount="100.00")
            _row(template, seed_periods[0], "100.00",
                 date(2026, 1, 1))
            _row(template, seed_periods[1], "100.00",
                 date(2026, 1, 1))

            _run_backfill()

            assert _versions(template) == [
                (date(2026, 1, 1), Decimal("100.00")),
            ]


class TestTheTransferArm:
    """The transfer arm runs the same derivation over different column names."""

    def test_a_transfer_price_change_is_mined(
        self, app, db, seed_user, seed_periods,
    ):
        """Production's money-market contribution: $500.00, then $250.00.

        The only transfer template whose rows record two prices, and the only
        exercise of the transfer arm's run-length encoding -- every other
        transfer assertion here reaches the seed arm instead, which an
        adversarial review pointed out ships the encoding untested on one of its
        two arms.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = _transfer_template(
                seed_user, savings, "Money Market Contribution", "250.00",
            )

            for amount, due, period in zip(
                ("500.00", "500.00", "250.00"),
                (date(2026, 4, 9), date(2026, 5, 7), date(2026, 5, 21)),
                seed_periods,
            ):
                _transfer_row(template, period, amount, due)

            _run_backfill()

            assert _versions(template, "transfer_template_id") == [
                (date(2026, 4, 9), Decimal("500.00")),
                (date(2026, 5, 21), Decimal("250.00")),
            ]

    def test_an_overridden_transfer_is_not_evidence(
        self, app, db, seed_user, seed_periods,
    ):
        """The evidence rule is the arm's own, not inherited by assumption.

        A grid full-edit of a transfer shadow's amount reaches the parent
        WITHOUT setting this flag (finding **N-245**), so the flag is not a
        complete filter here -- but a row that still HOLDS an override is
        excluded on this arm exactly as on the transaction one.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            template = _transfer_template(
                seed_user, savings, "Emergency Fund", "500.00",
            )

            _transfer_row(
                template, seed_periods[0], "999.99", date(2026, 4, 9),
                is_override=True,
            )
            _transfer_row(template, seed_periods[1], "500.00", date(2026, 5, 7))

            _run_backfill()

            assert _versions(template, "transfer_template_id") == [
                (date(2026, 5, 7), Decimal("500.00")),
            ]


# ── Re-runnability ───────────────────────────────────────────────────


class TestIdempotency:
    """A re-run after a partial failure inserts nothing new."""

    def test_running_twice_changes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The runs and seed arms are ``NOT EXISTS``-guarded; the tail self-limits.

        Stated precisely because an adversarial review read the earlier wording
        ("every arm is NOT EXISTS-guarded") and found the tail is not: it is
        idempotent because its disagreement SELECT goes empty once the scalar's
        version exists, which is a different mechanism and worth naming.
        """
        with app.app_context():
            template = _template(seed_user, amount="180.00")
            _row(template, seed_periods[0], "165.30",
                 date(2026, 4, 1), status=StatusEnum.DONE)
            _stamp(template, "transaction_templates", updated=date(2026, 6, 15))

            _run_backfill()
            first = _versions(template)
            _run_backfill()

            assert _versions(template) == first
            assert len(first) == 2
