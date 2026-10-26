"""Tests for commit C-18: stale-form prevention across every PATCH endpoint.

Mirrors the structure of the C-17 tests in test_accounts/test_stale_data.py:
schema invariants, lifecycle behaviour, true concurrent races, and per-route
stale-form / StaleDataError handling.  Every model that grew a
``version_id`` column in commit C-18 is exercised against the same
contract:

  1. The live database carries a NOT NULL ``version_id`` column with
     a positive CHECK constraint.
  2. The mapper declares ``version_id_col`` so SQLAlchemy narrows
     UPDATE/DELETE statements with ``WHERE version_id = ?``.
  3. Pure SELECT operations leave ``version_id`` unchanged; UPDATE
     bumps it by exactly one.
  4. A truly concurrent commit that bumps the row out from under
     the test session raises :class:`StaleDataError`.
  5. PATCH/POST endpoints that ship the row's ``version_id`` reject
     stale submissions.  HTMX endpoints render a 409 conflict cell;
     full-page form endpoints flash + redirect.
  6. Edit-form templates ship a hidden ``version_id`` input set to
     the current row's counter so the round-trip closes.

Together, these tests are the load-bearing automated check that
the optimistic-lock contract holds end-to-end.  Audit reference:
F-010 (High) / commit C-18 of the 2026-04-15 security remediation
plan.
"""

# pylint: disable=too-many-lines

from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.ref import (
    AccountType, CalcMethod, PaycheckLineKind, FilingStatus,
    RaiseType, Status, TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import account_service, transaction_service
from app.utils.dates import display_today
from app.models.amount_ownership import AmountOwnership
from app.services.amount_ownership import state_own_amount
from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    repriced_by_the_owner,
    start_test_pay_list,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _bump_version_outside_session(schema, table, row_id):
    """Bump ``version_id`` on a table row from a fresh DB connection.

    Simulates a concurrent commit by another browser tab.  Uses a
    connection independent of the test session so the calling
    session's identity map keeps the stale value while the database
    row carries the bumped value.

    The connection commit is essential: without it the UPDATE would
    sit in an open transaction and READ COMMITTED MVCC would hide
    the bump from the test session.
    """
    with db.engine.connect() as conn:
        conn.execute(
            text(
                f"UPDATE {schema}.{table} "
                "SET version_id = version_id + 1 "
                "WHERE id = :id"
            ),
            {"id": row_id},
        )
        conn.commit()


def _read_version(model, row_id):
    """Refetch a row and return its ``version_id`` after expiring the session."""
    db.session.expire_all()
    obj = db.session.get(model, row_id)
    return obj.version_id if obj is not None else None


# ── Test data factories (kept tight so each test reads as a story) ─


def _make_template(user_id, account_id, category_id):
    """Insert a TransactionTemplate and return it with id populated."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    template = TransactionTemplate(
        user_id=user_id,
        account_id=account_id,
        category_id=category_id,
        transaction_type_id=expense_type.id,
        name="Optimistic-Lock Test",
        default_amount=Decimal("100.00"),
        is_active=True,
    )
    db.session.add(template)
    db.session.commit()
    return template


def _make_transaction(seed_user, period):
    """Return a row OWNING `$50.00` in *period*: the ENGINE's row, re-priced by the owner.

    C-18's subject is the ROW's optimistic lock at the PATCH door: an amount
    edit writes the row and bumps its counter, and a race on that UPDATE is
    a 409.  A ONE-OFF's typed figure restates its DEFINITION (ruling
    R-BAL29; the definition's counter and the card's pin are graded in
    ``test_one_off_row_doors``), so the row is never UPDATEd and the race
    cannot fire -- which is why this fixture is a recurring definition's row
    made the owner's (:func:`generate_row_of` + :func:`repriced_by_the_owner`,
    the re-price door's two acts) rather than the one-off builder's row
    (plan step balance:X-bi-7c, ruling R-BAL60).
    """
    template = make_expense_template(
        db.session, seed_user, amount="50.00", name="Test Txn",
        category_key="Groceries",
    )
    txn = repriced_by_the_owner(generate_row_of(template, period), "50.00")
    db.session.commit()
    return txn


def _make_envelope_template_and_txn(seed_user, period):
    """Insert a tracked (envelope) definition + its engine-generated row; return both.

    The row is the definition's own (:func:`generate_row_of`, plan step
    balance:X-cf-4); the cases below read its ``version_id`` off the row
    rather than assuming one.
    """
    template = make_expense_template(
        db.session, seed_user, amount="400.00", name="Tracked Groceries",
        category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, period)
    db.session.commit()
    return template, txn


def _make_entry(txn_id, user_id):
    """Insert a TransactionEntry on the given transaction."""
    # The parent, resolved here rather than taken as an argument: this
    # helper's whole point is that a caller passes IDS.  The entry takes its
    # account (the parent's, because no door here says otherwise -- it is the
    # movement's OWN since plan step credit_card:CC-5-1) and its owner (the
    # parent's, which ``fk_transaction_entries_owner_transaction`` refuses to
    # see any other way).
    parent = db.session.get(Transaction, txn_id)
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn_id,
        account_id=parent.account_id,
        owner_id=parent.user_id,
        user_id=user_id,
        amount=Decimal("25.00"),
        description="Kroger",
        # The APP's today: the PATCH route re-posts this value and
        # ``_reject_future_purchase_date`` judges it on ``display_today()``
        # (ruling R-M), so a process date one day ahead is refused as a
        # future entry.  Same defect as the c19 concurrent-entry test.
        purchased_on=display_today(),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _make_savings_account(user_id):
    """Insert a savings Account that goals can reference."""
    savings_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=user_id,
            account_type_id=savings_type.id,
            name="Optimistic-Lock Savings",
            anchor_balance=Decimal("0.00"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _make_savings_goal(user_id, account_id):
    """Insert a fixed-mode savings goal."""
    goal = SavingsGoal(
        user_id=user_id,
        account_id=account_id,
        name="Emergency Fund",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)
    db.session.commit()
    return goal


def _make_salary_profile(user_id, scenario_id):
    """Insert a SalaryProfile and return it (no template / linked txns)."""
    filing_status = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing_status.id,
        name="Day Job",
        state_code="NC",
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("2307.69"))  # $60,000.00 a year / 26
    db.session.commit()
    return profile


def _make_salary_raise(profile_id):
    """Insert a SalaryRaise (percentage-based, recurring)."""
    raise_type = (
        db.session.query(RaiseType).filter_by(name="merit").one()
    )
    sraise = SalaryRaise(
        salary_profile_id=profile_id,
        raise_type_id=raise_type.id,
        effective_month=6,
        effective_year=2027,
        percentage=Decimal("0.03"),
    )
    db.session.add(sraise)
    db.session.commit()
    return sraise


def _make_paycheck_line(profile_id):
    """Insert a PaycheckLine (fixed amount, pre-tax)."""
    timing = (
        db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
    )
    method = (
        db.session.query(CalcMethod).filter_by(name="flat").one()
    )
    ded = PaycheckLine(
        salary_profile_id=profile_id,
        paycheck_line_kind_id=timing.id,
        calc_method_id=method.id,
        name="Health Insurance",
        amount=Decimal("100.00"),
    )
    db.session.add(ded)
    db.session.commit()
    return ded


def _make_transfer(seed_user, period):
    """Insert a transfer (and its two shadows) via the service."""
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    savings = _make_savings_account(seed_user["user"].id)
    projected = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    cat = seed_user["categories"]["Groceries"]
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("250.00")),
            status_id=projected.id,
            category_id=cat.id,
            name="Test Xfer",
        ),
    )
    db.session.commit()
    return xfer


def _make_transfer_template(user_id, from_acct_id, to_acct_id, category_id):
    """Insert a TransferTemplate."""
    tmpl = TransferTemplate(
        user_id=user_id,
        from_account_id=from_acct_id,
        to_account_id=to_acct_id,
        category_id=category_id,
        name="Monthly Savings",
        default_amount=Decimal("500.00"),
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.commit()
    return tmpl


# ═════════════════════════════════════════════════════════════════════
# Schema invariants
# ═════════════════════════════════════════════════════════════════════


# Each entry: (schema, table, model, check_constraint_name)
_VERSIONED_ROWS = [
    ("budget", "transactions", Transaction, "ck_transactions_version_id_positive"),
    ("budget", "transfers", Transfer, "ck_transfers_version_id_positive"),
    (
        "budget", "transaction_templates", TransactionTemplate,
        "ck_transaction_templates_version_id_positive",
    ),
    (
        "budget", "transfer_templates", TransferTemplate,
        "ck_transfer_templates_version_id_positive",
    ),
    ("budget", "savings_goals", SavingsGoal, "ck_savings_goals_version_id_positive"),
    (
        "budget", "transaction_entries", TransactionEntry,
        "ck_transaction_entries_version_id_positive",
    ),
    ("salary", "salary_profiles", SalaryProfile, "ck_salary_profiles_version_id_positive"),
    ("salary", "salary_raises", SalaryRaise, "ck_salary_raises_version_id_positive"),
    (
        "salary", "paycheck_lines", PaycheckLine,
        "ck_paycheck_lines_version_id_positive",
    ),
]


@pytest.mark.parametrize("schema, table, _model, _check", _VERSIONED_ROWS)
def test_version_id_column_present_and_not_null(app, schema, table, _model, _check):
    """Every versioned table carries a NOT NULL ``version_id``.

    A NULL counter would silently disable the optimistic lock on
    that row (``WHERE version_id IS NULL`` does not match the
    SQLAlchemy-emitted comparison), so this assertion is the
    minimal regression guard against a future migration drift.
    """
    with app.app_context():
        insp = inspect(db.engine)
        cols = {
            c["name"]: c
            for c in insp.get_columns(table, schema=schema)
        }

        assert "version_id" in cols, (
            f"{schema}.{table}.version_id column missing -- migration "
            f"a6c122211261 may not have run."
        )
        assert cols["version_id"]["nullable"] is False, (
            f"{schema}.{table}.version_id must be NOT NULL or the "
            f"optimistic lock silently fails on NULL counters."
        )


@pytest.mark.parametrize("schema, table, _model, check_name", _VERSIONED_ROWS)
def test_version_id_check_constraint_present(app, schema, table, _model, check_name):
    """Every versioned table has a ``version_id > 0`` CHECK.

    The CHECK is the database-tier guard against a future raw-SQL
    path that writes 0 or a negative value.  Its presence is
    asserted independently of the model declaration so a future
    edit that drops the constraint without removing it from the
    matching ``__table_args__`` block does not silently pass.
    """
    with app.app_context():
        insp = inspect(db.engine)
        checks = {
            c["name"]: c["sqltext"]
            for c in insp.get_check_constraints(table, schema=schema)
        }
        assert check_name in checks, (
            f"{check_name} missing from {schema}.{table} -- the schema "
            f"no longer matches the model declaration."
        )
        normalised = checks[check_name].lower().replace(" ", "")
        assert "version_id>0" in normalised, (
            f"CHECK constraint expression on {check_name} has changed; "
            f"rerun the migration or update the model in lockstep."
        )


@pytest.mark.parametrize("_schema, _table, model, _check", _VERSIONED_ROWS)
def test_mapper_declares_version_id_col(app, _schema, _table, model, _check):
    """Every versioned model exposes ``version_id_col`` on its mapper.

    Without this declaration SQLAlchemy emits ``UPDATE`` without
    the ``WHERE version_id = ?`` narrowing and the optimistic-lock
    contract collapses; the rest of the test class would still
    pass against the schema but production would silently regress.
    """
    with app.app_context():
        mapper = inspect(model)
        assert mapper.version_id_col is not None, (
            f"{model.__name__} mapper has no version_id_col -- "
            f"__mapper_args__ regression."
        )
        assert mapper.version_id_col.name == "version_id"


# ═════════════════════════════════════════════════════════════════════
# Lifecycle: insert, read, update, delete bumps
# ═════════════════════════════════════════════════════════════════════


class TestTransactionVersionLifecycle:
    """End-to-end behaviour of ``Transaction.version_id`` through the ORM."""

    def test_new_transaction_starts_at_version_one(
        self, app, seed_user, seed_periods,
    ):
        """``server_default='1'`` populates new rows at version 1.

        A FRESH row -- the one-off builder's, one INSERT and no UPDATE --
        rather than :func:`_make_transaction`'s re-priced row, whose
        re-price is that row's first UPDATE.
        """
        with app.app_context():
            txn = one_off_row_of(
                seed_periods[0], name="Test Txn", amount="50.00",
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            assert txn.version_id == 1

    def test_version_increments_only_on_update(
        self, app, seed_user, seed_periods,
    ):
        """Pure SELECT does not bump the counter; UPDATE bumps by one.

        A regression in either direction would be catastrophic:
        bumping on read would make every cached form stale; not
        bumping on update would make the lock no-op.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            v0 = txn.version_id
            txn_id = txn.id

            # Five reads must not bump the counter.
            for _ in range(5):
                _ = db.session.get(Transaction, txn_id).estimated_amount
                db.session.expire_all()
            assert _read_version(Transaction, txn_id) == v0

            # First update bumps by exactly one.
            txn = db.session.get(Transaction, txn_id)
            txn.notes = "updated"
            db.session.commit()
            assert _read_version(Transaction, txn_id) == v0 + 1

            # Second update bumps by exactly one more.
            txn = db.session.get(Transaction, txn_id)
            txn.notes = "updated again"
            db.session.commit()
            assert _read_version(Transaction, txn_id) == v0 + 2


@pytest.mark.parametrize(
    "factory_fn",
    [
        # Each factory is a callable taking (seed_user, seed_periods)
        # and returning the model id to bump.  Keeps the parameterised
        # test row-shape narrow.
        pytest.param("transfer", id="Transfer"),
        pytest.param("txn_template", id="TransactionTemplate"),
        pytest.param("xfer_template", id="TransferTemplate"),
        pytest.param("savings_goal", id="SavingsGoal"),
        pytest.param("salary_profile", id="SalaryProfile"),
        pytest.param("salary_raise", id="SalaryRaise"),
        pytest.param("paycheck_line", id="PaycheckLine"),
        pytest.param("transaction_entry", id="TransactionEntry"),
    ],
)
def test_concurrent_update_raises_stale_data_error(
    app, seed_user, seed_periods, factory_fn,
):
    """A version-bump out from under the test session raises StaleDataError.

    Same shape as test_concurrent_update_raises_stale_data_error
    in test_accounts/test_stale_data.py but parameterised across every C-18
    versioned model.  This is the load-bearing invariant that
    makes the SQLAlchemy tier of the optimistic lock work.
    """
    with app.app_context():
        period = seed_periods[0]
        if factory_fn == "transfer":
            obj = _make_transfer(seed_user, period)
            schema, table, model = "budget", "transfers", Transfer

            def mutate(o):
                state_own_amount(o, Decimal("999.99"))
        elif factory_fn == "txn_template":
            cat = seed_user["categories"]["Groceries"]
            obj = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            schema, table, model = (
                "budget", "transaction_templates", TransactionTemplate,
            )

            def mutate(o):
                o.default_amount = Decimal("999.99")
        elif factory_fn == "xfer_template":
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            obj = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            schema, table, model = (
                "budget", "transfer_templates", TransferTemplate,
            )

            def mutate(o):
                o.default_amount = Decimal("999.99")
        elif factory_fn == "savings_goal":
            savings = _make_savings_account(seed_user["user"].id)
            obj = _make_savings_goal(seed_user["user"].id, savings.id)
            schema, table, model = "budget", "savings_goals", SavingsGoal

            def mutate(o):
                o.target_amount = Decimal("999.99")
        elif factory_fn == "salary_profile":
            obj = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            schema, table, model = (
                "salary", "salary_profiles", SalaryProfile,
            )

            # A monetary column of the row (the yearly salary it mutated
            # went with plan step salary:X-av-3a).
            def mutate(o):
                o.additional_income = Decimal("99999.99")
        elif factory_fn == "salary_raise":
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            obj = _make_salary_raise(profile.id)
            schema, table, model = "salary", "salary_raises", SalaryRaise

            def mutate(o):
                o.percentage = Decimal("0.05")
        elif factory_fn == "paycheck_line":
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            obj = _make_paycheck_line(profile.id)
            schema, table, model = (
                "salary", "paycheck_lines", PaycheckLine,
            )

            def mutate(o):
                o.amount = Decimal("999.99")
        else:  # transaction_entry
            _tmpl, txn = _make_envelope_template_and_txn(seed_user, period)
            obj = _make_entry(txn.id, seed_user["user"].id)
            schema, table, model = (
                "budget", "transaction_entries", TransactionEntry,
            )

            def mutate(o):
                o.amount = Decimal("99.99")

        obj_id = obj.id
        assert _read_version(model, obj_id) == 1

        # Test session loads at version 1.
        loaded = db.session.get(model, obj_id)
        # Concurrent commit bumps the row to version 2.
        _bump_version_outside_session(schema, table, obj_id)
        # Test session attempts to mutate the stale row.
        mutate(loaded)
        with pytest.raises(StaleDataError):
            db.session.commit()
        db.session.rollback()

        # Persisted row carries the winner's version, not the loser's.
        assert _read_version(model, obj_id) == 2


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- HTMX endpoints (Transaction)
# ═════════════════════════════════════════════════════════════════════


class TestTransactionStaleFormPrevention:
    """``update_transaction`` (PATCH /transactions/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Submitting the row's current ``version_id`` updates and bumps."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v0 = txn.version_id

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "estimated_amount": "75.00",
                    "estimated_amount_as_rendered": "50.00",
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == Decimal("75.00")
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A submitted version older than the row's current counter returns 409.

        The route MUST short-circuit before touching the database;
        the financial value (``estimated_amount``) stays unchanged
        and the version counter is not bumped.  The 409 body
        carries the conflict cell so the user sees the latest value
        and a warning indicator.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            stale = txn.version_id

            _bump_version_outside_session("budget", "transactions", txn_id)
            db.session.expire_all()
            current = db.session.get(Transaction, txn_id).version_id
            assert current == stale + 1

            amount_before = db.session.get(Transaction, txn_id).estimated_amount

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "estimated_amount": "999.99",
                    "estimated_amount_as_rendered": "50.00",
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409, response.data
            body = response.data.decode()
            # Conflict cell carries the warning indicator.
            assert "exclamation-triangle" in body
            assert "text-warning" in body

            # No mutation, no version bump.
            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == amount_before
            assert persisted.version_id == current

    def test_route_catches_stale_data_error_as_409(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A StaleDataError raised at flush time becomes a 409 + conflict cell.

        Engineers a true race: the test session loads the row at
        version N, mutates it, then a SQLAlchemy mapper event fires
        during the UPDATE and bumps the row from a separate
        connection, defeating the version-pinned WHERE clause.
        SQLAlchemy raises StaleDataError and the route's except
        clause converts it into the same 409 + conflict cell the
        form-side check produces.
        """
        from sqlalchemy import event  # pylint: disable=import-outside-toplevel

        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            amount_before = txn.estimated_amount

            fired = {"flag": False}

            def make_stale(_mapper, _connection, target):
                if fired["flag"] or target.id != txn_id:
                    return
                fired["flag"] = True
                _bump_version_outside_session(
                    "budget", "transactions", txn_id,
                )

            event.listen(Transaction, "before_update", make_stale)
            try:
                response = auth_client.patch(
                    f"/transactions/{txn_id}",
                    data={"estimated_amount": "555.55", "estimated_amount_as_rendered": "50.00"},
                )
            finally:
                event.remove(Transaction, "before_update", make_stale)

            assert response.status_code == 409, response.data

            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.estimated_amount == amount_before

    def test_mark_done_catches_a_stale_settle_as_409(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """A race during a mark-done settle is a 409, not an unhandled 500.

        New coverage at plan step X-f2-c2, on a door that had none: the sibling
        above grades the PATCH edit, and nothing graded ``mark-done``.  The row
        is envelope-tracked WITH an entry so the settle takes the
        ``settle_from_entries`` branch rather than the manual one.

        The race is engineered as another writer's commit landing as the
        settle verb begins, after the door read the row: the row's version
        moves from a separate connection, so the session holds the stale
        version and the version-pinned WHERE fails.  That is where the settle
        verb took the row's lock until plan step ``balance:X-bn``, and where
        the developer placed the change (Round 12 Q1, 2026-09-23, "Move the
        change earlier": just before that lock, plan step
        ``credit_card:CC-5-4a-4``, ruling **R-CC96**).  Since X-bn no request
        of the owner's can land there -- every one waits on the owner's write
        lock (ruling **R-CC106**) -- so the other writer is one that holds no
        owner lock, which the separate connection is; a stale page's version
        is layer 2's to check (plan step ``balance:X-da``).  It was a
        ``before_update`` mapper event bumping the version DURING the UPDATE,
        as :meth:`test_route_catches_stale_data_error_as_409` still does,
        until the row lock made that connection wait on this request.  Shown
        to FIRE: deleting ``mark_done``'s ``except StaleDataError`` arm fails
        it.

        **It is deliberately NOT labelled the control for that step's
        exception-topology change, because it is not one, and the measurement
        is worth more than the label.**  Folding the settle into one verb put
        the amount-and-status phase inside the ``StaleDataError`` /
        ``IntegrityError`` net it used to sit outside, and the obvious claim --
        "that fixes a reachable 500" -- was tested against the pre-change route
        reconstructed from git and found FALSE: this test passes on both
        topologies.  The reason is worth recording, because it is not visible
        by reading the phases.  **The first flush of a settle is
        ``txn.status.is_settled``**, the ARGUMENT to the posting reconcile,
        evaluated after ``apply_status_change`` has both dirtied the row and
        expired its ``status`` relationship -- so the refresh autoflushes.  In
        both topologies that expression sits inside the net.  Everything
        earlier reads columns or already-loaded relationships: ``txn.entries``
        loads while the row is still clean, and ``settle_from_entries``' owner
        read is ``txn.user_id`` -- a COLUMN on the row already in the session,
        which loads nothing at all.  It walked ``txn.pay_period.user_id`` until
        plan step ``pay_calendar:C13-b``, and the conclusion is STRONGER for
        the move rather than merely surviving it: the read that had to be
        already-loaded is now one that cannot load.
        """
        with app.app_context():
            _template, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            txn_id = txn.id
            _make_entry(txn_id, seed_user["user"].id)
            status_before = txn.status_id

            fired = {"flag": False}
            real_settle = transaction_service.settle_transaction

            def make_stale_then_settle(row, *args, **kwargs):
                if not fired["flag"] and row.id == txn_id:
                    fired["flag"] = True
                    _bump_version_outside_session(
                        "budget", "transactions", txn_id,
                    )
                return real_settle(row, *args, **kwargs)

            monkeypatch.setattr(
                transaction_service, "settle_transaction", make_stale_then_settle,
            )
            response = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            )

            # The listener must have reached the row, or this is grading an
            # ordinary settle and would pass over the defect it exists for.
            assert fired["flag"], "the race never fired -- the control is dead"
            assert response.status_code == 409, response.data

            db.session.expire_all()
            persisted = db.session.get(Transaction, txn_id)
            assert persisted.status_id == status_before
            assert persisted.settled_on is None


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- HTMX endpoints (Transfer)
# ═════════════════════════════════════════════════════════════════════


class TestTransferStaleFormPrevention:
    """``update_transfer`` (PATCH /transfers/instance/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Submitting the row's current ``version_id`` updates and bumps."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v0 = xfer.version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "300.00",
                    "amount_as_rendered": "250.00",
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(Transfer, xfer_id)
            assert persisted.amount == Decimal("300.00")
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stale ``version_id`` returns 409 + conflict cell, no mutation."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            stale = xfer.version_id

            _bump_version_outside_session("budget", "transfers", xfer_id)
            db.session.expire_all()
            amount_before = db.session.get(Transfer, xfer_id).amount

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "999.99",
                    "amount_as_rendered": "250.00",
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409
            body = response.data.decode()
            assert "exclamation-triangle" in body
            assert "text-warning" in body

            db.session.expire_all()
            persisted = db.session.get(Transfer, xfer_id)
            assert persisted.amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- form-page endpoints (TransactionTemplate)
# ═════════════════════════════════════════════════════════════════════


class TestTransactionTemplateStaleFormPrevention:
    """``update_template`` (POST /templates/<id>) optimistic locking.

    Form-page route: stale-form check redirects with a flash
    warning rather than rendering an HTMX 409 partial.  Same
    semantics: no mutation, user reloads to retry.
    """

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user,
    ):
        """Matching version updates the template and bumps the counter."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v0 = tmpl.version_id
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            response = auth_client.post(
                f"/templates/{tmpl_id}",
                data={
                    "name": "Renamed Template",
                    "default_amount": "150.00",
                    "category_id": str(cat.id),
                    "transaction_type_id": str(expense_type.id),
                    "account_id": str(seed_user["account"].id),
                    "version_id": str(v0),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            db.session.expire_all()
            persisted = db.session.get(TransactionTemplate, tmpl_id)
            assert persisted.name == "Renamed Template"
            assert persisted.version_id == v0 + 1

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """A stale version flashes a warning and rolls the form back."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            stale = tmpl.version_id

            _bump_version_outside_session(
                "budget", "transaction_templates", tmpl_id,
            )
            db.session.expire_all()
            name_before = db.session.get(
                TransactionTemplate, tmpl_id,
            ).name
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            response = auth_client.post(
                f"/templates/{tmpl_id}",
                data={
                    "name": "Should Not Apply",
                    "default_amount": "999.99",
                    "category_id": str(cat.id),
                    "transaction_type_id": str(expense_type.id),
                    "account_id": str(seed_user["account"].id),
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(TransactionTemplate, tmpl_id)
            assert persisted.name == name_before, (
                "Stale-form must NOT mutate the template."
            )


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- form-page endpoints (TransferTemplate)
# ═════════════════════════════════════════════════════════════════════


class TestTransferTemplateStaleFormPrevention:
    """``update_transfer_template`` (POST /transfers/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on transfer-template update flashes a warning."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            tmpl_id = tmpl.id
            stale = tmpl.version_id

            _bump_version_outside_session(
                "budget", "transfer_templates", tmpl_id,
            )
            db.session.expire_all()
            name_before = db.session.get(TransferTemplate, tmpl_id).name

            response = auth_client.post(
                f"/transfers/{tmpl_id}",
                data={
                    "name": "Should Not Apply",
                    "default_amount": "9999.99",
                    "from_account_id": str(seed_user["account"].id),
                    "to_account_id": str(savings.id),
                    "category_id": str(cat.id),
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(TransferTemplate, tmpl_id)
            assert persisted.name == name_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- SavingsGoal
# ═════════════════════════════════════════════════════════════════════


class TestSavingsGoalStaleFormPrevention:
    """``update_goal`` (POST /savings/goals/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on savings-goal update flashes a warning."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            goal = _make_savings_goal(seed_user["user"].id, savings.id)
            goal_id = goal.id
            stale = goal.version_id

            _bump_version_outside_session("budget", "savings_goals", goal_id)
            db.session.expire_all()
            target_before = db.session.get(SavingsGoal, goal_id).target_amount

            response = auth_client.post(
                f"/savings/goals/{goal_id}",
                data={
                    "account_id": str(savings.id),
                    "name": "Renamed",
                    "target_amount": "99999.99",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SavingsGoal, goal_id)
            assert persisted.target_amount == target_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- SalaryProfile / SalaryRaise / PaycheckLine
# ═════════════════════════════════════════════════════════════════════


class TestSalaryProfileStaleFormPrevention:
    """``update_profile`` (POST /salary/<id>) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on salary-profile update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            profile_id = profile.id
            stale = profile.version_id
            filing_status = profile.filing_status_id

            _bump_version_outside_session(
                "salary", "salary_profiles", profile_id,
            )
            db.session.expire_all()
            # The update takes no salary since plan step salary:X-av-3a, so
            # the witness is the column the stale form tries to rename.
            name_before = db.session.get(
                SalaryProfile, profile_id,
            ).name

            response = auth_client.post(
                f"/salary/{profile_id}",
                data={
                    "name": "Renamed Job",
                    "filing_status_id": str(filing_status),
                    "state_code": "NC",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SalaryProfile, profile_id)
            assert persisted.name == name_before


class TestSalaryRaiseStaleFormPrevention:
    """``update_raise`` (POST /salary/raises/<id>/edit) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on raise update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            sraise = _make_salary_raise(profile.id)
            raise_id = sraise.id
            stale = sraise.version_id
            raise_type = sraise.raise_type_id

            _bump_version_outside_session(
                "salary", "salary_raises", raise_id,
            )
            db.session.expire_all()
            pct_before = db.session.get(SalaryRaise, raise_id).percentage

            response = auth_client.post(
                f"/salary/raises/{raise_id}/edit",
                data={
                    "raise_type_id": str(raise_type),
                    "effective_month": "6",
                    "effective_year": "2027",
                    "percentage": "10",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(SalaryRaise, raise_id)
            assert persisted.percentage == pct_before


class TestPaycheckLineStaleFormPrevention:
    """``update_line`` (POST /salary/lines/<id>/edit) optimistic locking."""

    def test_redirects_with_warning_on_stale_version(
        self, app, auth_client, seed_user,
    ):
        """Stale-form on deduction update flashes a warning."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            ded = _make_paycheck_line(profile.id)
            ded_id = ded.id
            stale = ded.version_id
            timing_id = ded.paycheck_line_kind_id
            method_id = ded.calc_method_id

            _bump_version_outside_session(
                "salary", "paycheck_lines", ded_id,
            )
            db.session.expire_all()
            amount_before = db.session.get(
                PaycheckLine, ded_id,
            ).amount

            response = auth_client.post(
                f"/salary/lines/{ded_id}/edit",
                data={
                    "name": "Renamed Deduction",
                    "paycheck_line_kind_id": str(timing_id),
                    "calc_method_id": str(method_id),
                    "amount": "999.99",
                    "amount_as_rendered": "250.00",
                    "version_id": str(stale),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"changed by another action" in response.data.lower()

            db.session.expire_all()
            persisted = db.session.get(PaycheckLine, ded_id)
            assert persisted.amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Stale-form prevention -- TransactionEntry
# ═════════════════════════════════════════════════════════════════════


class TestTransactionEntryStaleFormPrevention:
    """``update_entry`` (PATCH /transactions/<id>/entries/<id>) optimistic locking."""

    def test_succeeds_with_matching_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Matching version updates the entry and bumps the counter."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            v0 = entry.version_id

            response = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry_id}",
                data={
                    "amount": "30.00",
                    "amount_as_rendered": "250.00",
                    # The edit form emits the direction beside the amount
                    # since plan step ``bank_import:X-gj-2b-3``, and the
                    # route refuses one without the other rather than
                    # guessing a sign.  Posted here because this case is
                    # about the VERSION pin, and a payload the form cannot
                    # produce would make it about something else.
                    "direction": "charge",
                    "description": "Walmart",
                    "purchased_on": entry.purchased_on.isoformat(),
                    "version_id": str(v0),
                },
            )

            assert response.status_code == 200, response.data
            db.session.expire_all()
            persisted = db.session.get(TransactionEntry, entry_id)
            assert persisted.amount == Decimal("30.00")
            assert persisted.description == "Walmart"
            assert persisted.version_id == v0 + 1

    def test_returns_409_on_stale_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stale ``version_id`` returns 409 + entry list with warning banner."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            stale = entry.version_id

            _bump_version_outside_session(
                "budget", "transaction_entries", entry_id,
            )
            db.session.expire_all()
            amount_before = db.session.get(
                TransactionEntry, entry_id,
            ).amount

            response = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry_id}",
                data={
                    "amount": "9999.99",
                    "amount_as_rendered": "250.00",
                    "description": "Should Not Apply",
                    "purchased_on": entry.purchased_on.isoformat(),
                    "version_id": str(stale),
                },
            )

            assert response.status_code == 409, response.data
            body = response.data.decode()
            assert "changed by another action" in body.lower()

            db.session.expire_all()
            persisted = db.session.get(TransactionEntry, entry_id)
            assert persisted.amount == amount_before


# ═════════════════════════════════════════════════════════════════════
# Templates ship the version_id pin -- regression guards
# ═════════════════════════════════════════════════════════════════════


class TestEditTemplatesEmitVersionPin:
    """Edit forms must include ``<input type="hidden" name="version_id">``."""

    def test_transaction_quick_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid quick-edit template ships the txn's current version."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v = txn.version_id

            response = auth_client.get(f"/transactions/{txn_id}/quick-edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_full_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid full-edit popover ships the txn's current version."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v = txn.version_id

            response = auth_client.get(f"/transactions/{txn_id}/full-edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transfer_quick_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid transfer quick-edit ships the xfer's current version."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v = xfer.version_id

            response = auth_client.get(
                f"/transfers/quick-edit/{xfer_id}",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transfer_full_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid transfer full-edit ships the xfer's current version."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v = xfer.version_id

            response = auth_client.get(
                f"/transfers/{xfer_id}/full-edit",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_template_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The recurring-transaction edit form ships the row's version."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v = tmpl.version_id

            response = auth_client.get(f"/templates/{tmpl_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_template_create_form_omits_version(
        self, app, auth_client,
    ):
        """The create form has no version pin (no row to lock yet).

        Mirrors test_account_create_form_omits_version_pin in
        test_accounts/test_stale_data.py: a copy-paste regression that put a
        ``template.version_id`` reference into the create path
        would Jinja-error against ``template = None``.
        """
        with app.app_context():
            response = auth_client.get("/templates/new")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' not in body

    def test_transfer_template_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The recurring-transfer edit form ships the row's version."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v = tmpl.version_id

            response = auth_client.get(f"/transfers/{tmpl_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_savings_goal_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The savings-goal edit form ships the row's version."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            goal = _make_savings_goal(seed_user["user"].id, savings.id)
            goal_id = goal.id
            v = goal.version_id

            response = auth_client.get(
                f"/savings/goals/{goal_id}/edit",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_salary_profile_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The salary-profile edit form ships the row's version."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            profile_id = profile.id
            v = profile.version_id

            response = auth_client.get(f"/salary/{profile_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_salary_raise_edit_button_carries_version(
        self, app, auth_client, seed_user,
    ):
        """The raise edit button carries ``data-raise-version-id``.

        Salary raises share a single form for create and edit; the
        version pin is plumbed into the form by app.js when the user
        clicks the edit button, which carries the row's version_id
        as a data attribute.  This test asserts the data attribute
        is rendered server-side so the JS hand-off cannot regress
        silently.
        """
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            sraise = _make_salary_raise(profile.id)
            v = sraise.version_id

            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'data-raise-version-id="{v}"' in body
            ), (
                "Raise edit button must include data-raise-version-id "
                "so app.js can populate the form's hidden version "
                "input on edit."
            )

    def test_paycheck_line_edit_button_carries_version(
        self, app, auth_client, seed_user,
    ):
        """The deduction edit button carries ``data-line-version-id``.

        Same shape as the raise test: the edit button surfaces the
        row's current version as a data attribute that app.js wires
        into the shared add/edit form.
        """
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            ded = _make_paycheck_line(profile.id)
            v = ded.version_id

            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'data-line-version-id="{v}"' in body
            ), (
                "Deduction edit button must include "
                "data-line-version-id so app.js can populate the "
                "form's hidden version input on edit."
            )

    def test_entry_inline_edit_form_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The entry inline-edit form ships the row's version_id."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            v = entry.version_id

            response = auth_client.get(
                f"/transactions/{txn.id}/entries?editing={entry_id}",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body
