"""C-25 boolean NOT NULL + server_default invariants.

Exercises the storage-tier guarantees added by migration
``c5d20b701a4e_c25_boolean_notnull_and_server_default_sweep.py``.
The application-tier guarantees (Python ``default=`` on the ORM
column) are exercised separately by the model unit tests; this file
isolates the DB-only path so a future regression that drops a
server_default or relaxes a NOT NULL is caught here even if every ORM
write still succeeds because SQLAlchemy is filling in the column
client-side.

Each test queries ``pg_attribute`` / ``pg_attrdef`` directly so the
assertion targets the live database state -- the very surface the
audit finding said was drifting.  An ORM-level check would not catch
the case where the model and migration disagree silently.

Audit reference: F-068 + F-134 / commit C-25 of the 2026-04-15
security remediation plan.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from tests._test_helpers import bare_expense_template


# ---------------------------------------------------------------------------
# F-068 boolean / sort_order columns -- must be NOT NULL with server_default
# ---------------------------------------------------------------------------

# Each entry: (schema, table, column, expected_server_default_text).
#
# ``expected_server_default_text`` is the substring expected to appear
# in pg_get_expr() for the column's default.  PostgreSQL's
# string-cast behaviour means the rendered form may include a type
# annotation (e.g. ``'NC'::character varying`` for a String default),
# so the assertion uses substring match rather than equality.
F_068_LOCKED_COLUMNS = [
    ("auth", "users", "is_active", "true"),
    ("auth", "mfa_configs", "is_enabled", "false"),
    ("budget", "accounts", "is_active", "true"),
    ("budget", "accounts", "sort_order", "0"),
    ("budget", "categories", "is_active", "true"),
    ("budget", "categories", "sort_order", "0"),
    ("budget", "recurrence_rules", "interval_n", "1"),
    ("budget", "savings_goals", "is_active", "true"),
    ("budget", "scenarios", "is_baseline", "false"),
    ("budget", "transaction_templates", "is_active", "true"),
    ("budget", "transaction_templates", "sort_order", "0"),
    ("budget", "transactions", "is_override", "false"),
    ("budget", "transactions", "is_deleted", "false"),
    ("budget", "transfer_templates", "is_active", "true"),
    ("budget", "transfer_templates", "sort_order", "0"),
    ("salary", "paycheck_lines", "inflation_enabled", "false"),
    ("salary", "paycheck_lines", "is_active", "true"),
    ("salary", "paycheck_lines", "sort_order", "0"),
    ("salary", "pension_profiles", "is_active", "true"),
    ("salary", "salary_profiles", "is_active", "true"),
    ("salary", "salary_profiles", "sort_order", "0"),
    ("salary", "salary_raises", "is_recurring", "false"),
    ("salary", "tax_brackets", "sort_order", "0"),
]


# F-134 columns -- already NOT NULL, but server_default lost on the live DB.
F_134_RESTORED_COLUMNS = [
    ("budget", "transfers", "is_override", "false"),
    ("budget", "transfers", "is_deleted", "false"),
    ("budget", "investment_params", "assumed_annual_return", "0.07000"),
    # #38: ``employer_contribution_type`` (the free-string column with a
    # ``'none'`` server_default) was promoted to the ref FK
    # ``employer_contribution_type_id`` -- a ref FK carries no
    # server_default (an id is not a static literal), so it is no longer
    # an F-134 restored-default column.
    ("salary", "fica_configs", "ss_rate", "0.0620"),
    ("salary", "fica_configs", "ss_wage_base", "176100"),
    ("salary", "fica_configs", "medicare_rate", "0.0145"),
    ("salary", "fica_configs", "medicare_surtax_rate", "0.0090"),
    ("salary", "fica_configs", "medicare_surtax_threshold", "200000"),
    ("salary", "pension_profiles", "name", "'Pension'"),
    ("salary", "pension_profiles", "consecutive_high_years", "4"),
    ("salary", "salary_profiles", "state_code", "'NC'"),
    ("salary", "salary_profiles", "qualifying_children", "0"),
    ("salary", "salary_profiles", "other_dependents", "0"),
    ("salary", "salary_profiles", "additional_income", "0"),
    ("salary", "salary_profiles", "additional_deductions", "0"),
    ("salary", "salary_profiles", "extra_withholding", "0"),
]


def _column_metadata(session, schema, table, column):
    """Return ``(attnotnull, server_default_text)`` for one column.

    Reads the catalog directly so the value reflects what PostgreSQL
    will actually do for an INSERT, not what SQLAlchemy reflected at
    metadata construction time.
    """
    row = session.execute(db.text(
        "SELECT a.attnotnull, "
        "       pg_get_expr(d.adbin, d.adrelid) AS server_default "
        "FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_attrdef d "
        "    ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE n.nspname = :schema "
        "  AND c.relname = :table "
        "  AND a.attname = :column "
        "  AND a.attnum > 0 "
        "  AND NOT a.attisdropped"
    ), {"schema": schema, "table": table, "column": column}).one()
    return row.attnotnull, row.server_default


@pytest.mark.parametrize(
    "schema, table, column, expected_default",
    F_068_LOCKED_COLUMNS,
)
def test_f068_columns_are_not_null_with_server_default(
    db, schema, table, column, expected_default,  # pylint: disable=unused-argument
):
    """Every F-068 column is NOT NULL with the documented server_default.

    Reads ``pg_attribute.attnotnull`` and ``pg_attrdef.adbin`` directly
    so the assertion targets the live storage tier.  A future
    regression that drops the constraint or default surfaces here
    even if every ORM write still happens to succeed.
    """
    not_null, server_default = _column_metadata(
        db.session, schema, table, column,
    )
    assert not_null is True, (
        f"{schema}.{table}.{column} is nullable; expected NOT NULL "
        f"per F-068 / C-25"
    )
    assert server_default is not None, (
        f"{schema}.{table}.{column} has no server_default; expected "
        f"{expected_default!r} per F-068 / C-25"
    )
    assert expected_default in server_default, (
        f"{schema}.{table}.{column} server_default is "
        f"{server_default!r}, expected to contain {expected_default!r}"
    )


@pytest.mark.parametrize(
    "schema, table, column, expected_default",
    F_134_RESTORED_COLUMNS,
)
def test_f134_columns_have_restored_server_default(
    db, schema, table, column, expected_default,  # pylint: disable=unused-argument
):
    """Every F-134 column carries its documented server_default again.

    These columns were already NOT NULL before C-25 -- only the
    server_default was missing, so the assertion targets the default
    expression alone.
    """
    not_null, server_default = _column_metadata(
        db.session, schema, table, column,
    )
    assert not_null is True, (
        f"{schema}.{table}.{column} unexpectedly relaxed to nullable; "
        f"F-134 columns must remain NOT NULL"
    )
    assert server_default is not None, (
        f"{schema}.{table}.{column} has no server_default; expected "
        f"{expected_default!r} per F-134 / C-25"
    )
    assert expected_default in server_default, (
        f"{schema}.{table}.{column} server_default is "
        f"{server_default!r}, expected to contain {expected_default!r}"
    )


# ---------------------------------------------------------------------------
# Behavioural checks -- prove the server_default actually fires
# ---------------------------------------------------------------------------


def test_server_default_fills_omitted_boolean_on_raw_insert(db, seed_user):
    """A raw INSERT that omits a boolean column receives the server_default.

    This is the core invariant F-068 protects: a code path that
    bypasses the ORM (raw SQL, audit trigger, future job, ``pg_dump``
    reload) never lands NULL into a boolean column whose semantics
    have no NULL meaning.

    Uses ``budget.scenarios.is_baseline`` because (a) the table has no
    ``updated_at`` mixin to satisfy and (b) the column's default
    (``false``) is the safer side of the boolean -- a row created
    without an explicit value is non-baseline by default.
    """
    user_id = seed_user["user"].id
    db.session.execute(db.text(
        "INSERT INTO budget.scenarios (user_id, name) "
        "VALUES (:user_id, :name)"
    ), {"user_id": user_id, "name": "Raw insert no flag"})
    db.session.commit()
    row = db.session.execute(db.text(
        "SELECT is_baseline FROM budget.scenarios "
        "WHERE user_id = :user_id AND name = :name"
    ), {"user_id": user_id, "name": "Raw insert no flag"}).one()
    assert row.is_baseline is False, (
        "server_default 'false' did not fire on raw INSERT; "
        "F-068 invariant violated"
    )


def test_explicit_null_boolean_rejected_after_lock(db, seed_user):
    """Inserting NULL into a locked boolean column raises NotNullViolation.

    F-068 columns have no NULL meaning; a code path that tries to set
    one to NULL is a bug, and PostgreSQL must catch it at the
    storage tier.
    """
    user_id = seed_user["user"].id
    with pytest.raises(IntegrityError):
        db.session.execute(db.text(
            "INSERT INTO budget.scenarios "
            "(user_id, name, is_baseline) "
            "VALUES (:user_id, :name, NULL)"
        ), {"user_id": user_id, "name": "Null flag attempt"})
        db.session.commit()
    db.session.rollback()


def test_server_default_fills_omitted_sort_order_integer(db, seed_user):
    """A raw INSERT that omits ``sort_order`` receives the integer default 0.

    Mirrors the boolean case for the integer columns swept by F-068.
    Uses ``budget.categories.sort_order`` because the parent row
    needs only ``user_id`` plus group/item names.
    """
    user_id = seed_user["user"].id
    db.session.execute(db.text(
        "INSERT INTO budget.categories (user_id, group_name, item_name) "
        "VALUES (:user_id, 'C25 Group', 'C25 Item')"
    ), {"user_id": user_id})
    db.session.commit()
    row = db.session.execute(db.text(
        "SELECT sort_order, is_active FROM budget.categories "
        "WHERE user_id = :user_id AND group_name = 'C25 Group'"
    ), {"user_id": user_id}).one()
    assert row.sort_order == 0
    assert row.is_active is True


def test_server_default_fills_omitted_recurrence_integers(db, seed_user):
    """A raw INSERT omitting ``interval_n`` gets 1.

    It is a logic-bearing divisor -- the modulus in the PERIOD-unit occurrence
    walk and the divisor in every monthly equivalent -- and its CHECK
    (``interval_n > 0``) treats a NULL operand as satisfied, so before this
    migration a raw INSERT could land a meaningless NULL.  The server_default
    must fill the documented logical default on a storage-tier-only INSERT.

    **``offset_periods`` left this case at plan step R7c-c** with the column.
    It was the second logic-bearing integer here -- the cycle phase, under
    ``offset_periods >= 0`` -- and the phase is derived from the rule's first
    occurrence on every read now, so there is no stored value for a default to
    fill.
    """
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import (  # pylint: disable=import-outside-toplevel
        BusinessDayShiftEnum,
        PeriodPlacementEnum,
        RecurrenceUnitEnum,
    )

    # The cadence columns are stated because plan step R7c-b made them NOT
    # NULL, and the OWNER because plan step R-F6 made a rule belong to exactly
    # one definition (``ck_recurrence_rules_one_owner``).  Omitting either
    # would make this INSERT fail before the server_default under test was
    # ever reached -- the same IntegrityError from a different cause, on a
    # case whose whole point is that a storage-tier-only INSERT lands the
    # documented default.
    owner_id = bare_expense_template(db.session, seed_user).id
    db.session.execute(db.text(
        "INSERT INTO budget.recurrence_rules "
        "(transaction_template_id, unit_id, placement_id, shift_id, starts_on) "
        "VALUES (:owner_id, :unit_id, :placement_id, :shift_id, "
        "DATE '2026-01-02')"
    ), {
        "owner_id": owner_id,
        "unit_id": ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD),
        "placement_id": ref_cache.period_placement_id(
            PeriodPlacementEnum.CONTAINING_DATE,
        ),
        "shift_id": ref_cache.business_day_shift_id(
            BusinessDayShiftEnum.NONE,
        ),
    })
    db.session.commit()
    row = db.session.execute(db.text(
        "SELECT interval_n FROM budget.recurrence_rules "
        "WHERE transaction_template_id = :owner_id"
    ), {"owner_id": owner_id}).one()
    assert row.interval_n == 1


def test_transactions_is_override_and_is_deleted_default_to_false(
    db, seed_user, seed_periods,
):
    """Raw INSERT into transactions defaults is_override / is_deleted to False.

    Both columns were swept by F-068 and the migration's NOT NULL +
    server_default('false') combination must hold.  Uses a raw INSERT
    rather than an ORM-driven create so the test exercises the
    storage tier exclusively.
    """
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

    db.session.execute(db.text(
        "INSERT INTO budget.transactions "
        "(template_id, user_id, account_id, pay_period_id, scenario_id, "
        " status_id, name, transaction_type_id, estimated_amount, due_date, "
        " occurs_on) "
        "VALUES (:tid, :uid, :acct, :pp, :sc, :st, :name, :tt, :amt, :due, "
        "        :due)"
    ), {
        # The PRICING LINK and the day, stated for the reason the owner is
        # (plan step ``balance:X-bi-7d-1``): the family's cutover makes a
        # link-less row unstorable (``ck_transactions_one_pricing_link``
        # at ``= 1``) and a linked row must be dated, so a storage-tier
        # control names a rule-less definition of the owner's and its
        # paycheck's start.  Neither column is this case's subject.
        "tid": bare_expense_template(db.session, seed_user).id,
        "due": seed_periods[0].start_date,
        # The OWNER, stated because this INSERT bypasses the ORM on purpose
        # (plan step ``pay_calendar:C13-a``): ``budget.transactions.user_id`` is
        # NOT NULL and has no server default, exactly like the four ids beside
        # it, so a storage-tier control has to say it.
        "uid": seed_periods[0].user_id,
        "acct": seed_user["account"].id,
        "pp": seed_periods[0].id,
        "sc": seed_user["scenario"].id,
        "st": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "C-25 raw txn",
        "tt": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "amt": Decimal("12.34"),
    })
    db.session.commit()
    row = db.session.execute(db.text(
        "SELECT is_override, is_deleted FROM budget.transactions "
        "WHERE name = :name AND scenario_id = :sc"
    ), {"name": "C-25 raw txn", "sc": seed_user["scenario"].id}).one()
    assert row.is_override is False
    assert row.is_deleted is False


def test_pension_profiles_name_default_fires_on_raw_insert(db, seed_user):
    """F-134 server_default 'Pension' fills name when omitted from INSERT.

    PensionProfile's ``name`` was created with
    ``server_default=sa.text("'Pension'")`` in the original retirement
    migration but the live database had lost it.  C-25 restores the
    default; an INSERT that omits the column must now succeed and
    land 'Pension' in the row.
    """
    user_id = seed_user["user"].id
    # Required columns: user_id, benefit_multiplier, hire_date.
    db.session.execute(db.text(
        "INSERT INTO salary.pension_profiles "
        "(user_id, benefit_multiplier, hire_date) "
        "VALUES (:user_id, 0.01500, :hd)"
    ), {"user_id": user_id, "hd": date(2020, 1, 1)})
    db.session.commit()
    row = db.session.execute(db.text(
        "SELECT name, consecutive_high_years, is_active "
        "FROM salary.pension_profiles WHERE user_id = :user_id"
    ), {"user_id": user_id}).one()
    assert row.name == "Pension"
    assert row.consecutive_high_years == 4
    assert row.is_active is True


def test_fica_configs_rate_defaults_fire_on_raw_insert(db, seed_user):
    """F-134 numeric server_defaults restore the documented FICA constants.

    A raw INSERT that supplies only ``user_id`` and ``tax_year``
    should land 6.20% / 1.45% / 0.90% / 200,000 / 176,100 in the
    rate columns -- the rates that were the original migration's
    defaults but had drifted off the live DB before C-25.
    """
    user_id = seed_user["user"].id
    db.session.execute(db.text(
        "INSERT INTO salary.fica_configs (user_id, tax_year) "
        "VALUES (:user_id, 2030)"
    ), {"user_id": user_id})
    db.session.commit()
    row = db.session.execute(db.text(
        "SELECT ss_rate, ss_wage_base, medicare_rate, "
        "       medicare_surtax_rate, medicare_surtax_threshold "
        "FROM salary.fica_configs "
        "WHERE user_id = :user_id AND tax_year = 2030"
    ), {"user_id": user_id}).one()
    assert row.ss_rate == Decimal("0.0620")
    assert row.ss_wage_base == Decimal("176100.00")
    assert row.medicare_rate == Decimal("0.0145")
    assert row.medicare_surtax_rate == Decimal("0.0090")
    assert row.medicare_surtax_threshold == Decimal("200000.00")
